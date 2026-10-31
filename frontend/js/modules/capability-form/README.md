# capability-form — ACP 3.0 能力表单模块（A-59 / A-60 / P23）

- 模块：`frontend/js/modules/capability-form/module.js`
- 样式：`frontend/css/modules/capability-form.css`（仅 `.acp-capf*` 作用域，不新增全局 token）
- 测试：`frontend/js/module_tests/acp2_capability_form_module.test.js`（20 项）
- 注册名：`window.ACPNextModules["capability-form"] = { descriptor, factory }`
- 测试命令：
  `node frontend/js/module_tests/run_all.js acp2_capability_form_module`
- 数据来源：`GET /api/v2/science/capability-forms`（经 `ACPClient.science.fetchCapabilityForms`），
  `POST /api/v2/science/preflight`（经 `ACPClient.science.preflight`），
  `POST /api/v1/jobs`（经 `ACPClient.researchCommands.submit`）

## 0. 实现取舍：紧凑模块，不包装经典 `ACPCapabilityForm`

结论：**没有包装** `frontend/js/workbench/features/capability-form.js`，而是实现了一个消费同一份
表单文档的紧凑模块。理由（按“不读页面全局 / 生命周期可控”判定）：

1. 经典 feature 挂载到**固定 id** `#capability-form-body`，并从页面全局读取
   `window.ACPWorkbench.contracts.uiSubset`、`t()`，还会创建 `window.ACPCapabilityForm` 与
   `ACPWorkbench.features.*`；把它加载进 `/next/` 会引入 Shell 不拥有的页面全局。
2. 它没有模块四态（`data-acp-module-state`）、generation 守卫与 dispose 语义，而这三项是
   A-59/A-60 的通用验收项；外面再包一层等于把渲染/生命周期重写一遍。
3. 它的 `mount(document)` 只有一个固定根节点，无法接受 Shell panel 的 host 元素。

本模块**复用同一份契约与同一套校验实现**：`js/workbench/contracts/ui_subset.js` 的
`tablesMatch / validateFormDocument / fieldModel / validateValues / buildFormOutcome`。
模块自身只做编排（取数、选中、字段状态、渲染、预检/提交状态、审批绑定），**没有第二套
JSON-Schema/子集校验器**（源码门禁测试断言不出现 `REJECTED_KEYWORDS` /
`additionalProperties` / 本地 `fetch`）。

## 1. 依赖与契约

| 依赖 | 注入方式 | 用途 |
|---|---|---|
| `science.fetchCapabilityForms(scope, opts)` | `deps.science`，缺省 `window.ACPClient.science` | 取 `acp2.ui.capability-forms.v1` 文档 |
| `science.preflight(body, scope, opts)` | 同上 | 零副作用预检，返回 `plan.plan_digest` / `preflight_fingerprint` / `estimate` |
| `commands.submit(scope, body, opts)` | `deps.commands`，缺省 `window.ACPClient.researchCommands` | 已批准计划的提交；只认真实 `TaskRef` |
| `ACPWorkbench.contracts.uiSubset` | 页面脚本（`ui_subset.js`） | 子集表、字段模型、值校验、请求体构建 |
| `context`（WorkspaceContext） | `deps.context` | `projectId` 来源；`subscribe` 变化即重载 |
| `i18n` | `window.ACPShell.i18n`（Shell 字典） | 文案；缺键时使用模块内联 zh（模块不另造字典） |

模块内不出现 URL 前缀、不发 `fetch`、不建第二套缓存；`opts.generation` 传给科学客户端，
便于其缓存分组（不改变客户端语义）。

**服务端文档形状**（`src/acp/api/capability_forms.py`）：`schema` / `subset_version` /
`subset`（含 `tablesMatch` 校验表）/ `subset_digest` / `forms[]`（`available` 或 `unavailable`
+ typed `error`）/ `read_only[]`。模块在解释任何表单前先做：
schema 匹配、subset 版本匹配、`tablesMatch`、每个 available 表单的
`validateFormDocument`、`form_digest === form.form_digest`、form/entry 的
`capability_id` 一致性。任一不满足 → 四态中的 `error`，绝不渲染成可提交的空表单。

## 2. 模块 API

```js
var instance = ACPNextModules["capability-form"].factory({
  context,          // WorkspaceContext（可只读；projectId 变化会重载）
  science,          // ACPClient.science（或 mock）
  commands,         // ACPClient.researchCommands（或 mock）
  approval,         // 可选：已批准记录（A-57/A-58 的审批卡注入）
  projectId,        // 可选：无 context 时的初始项目
  approval          // 可选：已批准记录（A-57/A-58 的审批卡注入）
});
await instance.mount(hostEl, initialSnapshot?); // 面板挂载
instance.updateContext(snapshot);               // Shell 推送上下文
instance.setProject("proj-1");                  // 显式切项目（返回 Promise）
instance.load({ projectId: "proj-1" });         // 重载表单文档
instance.select("electronic.energy");           // 切换能力
instance.setValue("charge", 2);                 // 写字段（未知/未绑定字段抛 typed error）
instance.validate();                            // 校验当前值（共享子集校验器）
instance.validate(candidateValues);             // 干跑：未知字段仍报 ui_value_unknown_field
instance.buildRequest();                        // 校验 + 构体（失败抛 ui_value_*）
await instance.preflight({ projectId });        // 预检；失败抛 typed error
await instance.submit({ projectId }, { approval, idempotencyKey }); // 提交
instance.approval(approvedRecord);              // 绑定/替换/清除（null）审批
instance.getState();                            // 只读快照（见 §4）
instance.render(); instance.suspend(); instance.resume(); instance.dispose();
```

descriptor：`id="capability-form"`、`interfaceVersion=1`、`version="0.2.0"`、
`provides=["research.capability-form"]`、`binds=["projectId"]`、`singleton` 未声明（由 Shell 决定）。

## 3. 四态、守卫与释放

- `data-acp-module-state="empty|loading|ready|error"`（与 `molecule-viewer` / `energy-profile` 一致）。
  - `empty`：无 `projectId`（`data-acp-empty-reason="no_project"`）或文档无任何表单/历史
    （`no_forms`）；
  - `loading`：取文档中；
  - `ready`：文档已通过守卫；`read_only` / `unavailable` 条目也可进入 ready 并只读展示；
  - `error`：取数失败、契约缺失、文档/表单非法；根节点 `data-acp-error-code` 带 typed code，
    附带“重试”按钮（`data-acp-action="retry"`）。
- **提交状态**（A-60 稳定机器钩子）：模块根与提交结果框都暴露
  `data-acp-submit-state="idle|submitting|submitted|unconfirmed|error|conflict"`；
  结果框同时保留 A-59 的 `data-acp-submit`（同值）。失败时结果框另有
  `data-acp-error-code`（映射后的稳定可读码）与 `data-acp-error-category`
  （`input|access|not_found|conflict|validation|network|http|local`）；模块根同样把当前
  提交错误的映射码放到 `data-acp-error-code`（并镜像为 `data-acp-submit-error-code`），
  ready 态下加载错误与提交错误不会同时出现。`conflict` 是 409 的“需重新复核”状态，不是成功。
- **generation 守卫**：`load` / `select` / `setProject` / `dispose` 都会 bump generation；
  迟到的文档、预检、提交结果一律丢弃（抛 `stale_result`，不改状态）。字段编辑另用 edit token
  使在途预检失效（`preflight_state` 派生为 `stale`）。
- `dispose()`：取消 context 订阅、bump generation、从 host 移除根节点、清空进入空态；
  幂等；释放后可重新 `mount` 开始新生命周期。

## 4. 渲染与校验

- 渲染只用 `createElement` + `textContent`；远端 `title` / `description` / `error.message`
  不会成为 markup（测试断言无 `innerHTML` / `insertAdjacentHTML`）。
- 字段模型来自 `uiSubset.fieldModel(form)`：label、required 标记、widget（textarea / select /
  checkbox / number / text）、默认值、`minimum/maximum`（`min`/`max`/`step`）、单位与说明。
- 值校验完全委托 `uiSubset.validateValues`：required / type / enum / bounds / minLength /
  maxLength / pattern / format；错误按 `path` 映射到字段：
  `state.errors[field] = [{ code, path, message }]`，并在字段下渲染
  `data-acp-field-error=<field>` + `data-acp-code=<code>`；表单级项进入
  `data-acp-problems` 列表。
- **未知字段**：`setValue("magic", …)` 抛 `ui_value_unknown_field` 并记录到
  `state.errors.magic`（不写入 `values`，不静默丢弃）；`validate({..., magic:1})` 也会在
  `errors.magic` 报告。
- **未绑定字段**：字段缺 `x-acp.binding` 时文档守卫失败（`ui_binding_missing`），整张表单
  不渲染为输入；运行期再遇未绑定字段同样抛 typed error。
- 枚举字段渲染一个空选项（“请选择”），不静默选中第一项；数字输入沿用经典 feature 的
  `badInput`/空值语义（空串 ≠ 0，非法数字交给校验器拒绝）。
- 输入控件在预检/提交进行中禁用（`data-acp-notice="inputs_locked"`）。

## 5. 预检、审批与提交

- **预检**：先本地 `buildRequest()`；不合法 → 只渲染字段错误，**不会调用** `science.preflight`。
  成功记录 `plan.plan_digest`、`preflight_fingerprint`、`estimate`、预检时的
  `values_revision` 与 `body/fields`；编辑任何字段后派生为 `stale`（`data-acp-preflight="stale"`），
  必须重新预检。预检/提交在飞时第二个 `preflight()` 本地拒绝 `preflight_busy`。
- **审批可见**：`data-acp-approval="required|not_approved|mismatch|granted"` 的通知常驻；
  未绑定/未批准/计划摘要不匹配时提交按钮禁用（`data-acp-submit-blocked="approval"`），
  且 `submit()` 本地拒绝（`approval_required` / `approval_not_granted` /
  `approved_plan_digest_mismatch`），不触达提交客户端。审批记录由 A-57/A-58 的审批卡通过
  `approval(record)`（或 `deps.approval`）注入，模块不自行审批。
- **提交**：必须同时满足“表单合法 + 预检 ok 且未过期 + 项目一致 + 已批准记录”。
  `submit` body 是预检过的同一份 body，并附加 `plan_digest` 供
  `researchCommands.bindSubmission` 做防篡改绑定；`idempotencyKey` 优先取调用参数，否则用
  每次成功预检生成一次的 key。只经注入的 `commands.submit`（默认
  `ACPClient.researchCommands`）；模块自身无 fetch、无 URL 前缀。预检/提交在飞时提交按钮
  `disabled`（`data-acp-submit-blocked="busy"`），`submit()` 本地拒绝 `submit_busy`。
- **不做假成功**：只有返回真实 `job_id` + `attempt`（或 `task_ref` 中同义的 job/task id +
  attempt）才进入 `submitted` 并渲染回执；缺 TaskRef、缺 attempt、网络/5xx/无状态 →
  `unconfirmed`；本地拒绝或 4xx → `error`。`getState().task_ref` 仅在真实 TaskRef 时非 null。
- **A-60 错误映射**：`submit()` 抛出的错误保留原始 `code`（程序调用方使用），同时附
  `displayCode` / `category` / `submitState`；DOM 的 `data-acp-error-code` 使用稳定映射码：

  | 服务端结果 | `data-acp-submit-state` | `data-acp-error-code` | `category` |
  |---|---|---|---|
  | 400 | `error` | `input_invalid` | `input` |
  | 401 / 403 | `error` | `access_denied` | `access` |
  | 404 | `error` | `not_found` | `not_found` |
  | 409（revision / approval 冲突） | `conflict` | `revision_conflict` | `conflict` |
  | 422 | `error` | `validation_failed` | `validation` |
  | 5xx | `unconfirmed` | `submit_unconfirmed` | `network` |
  | 网络/超时/无状态 | `unconfirmed` | `submit_unconfirmed` | `network` |

  HTTP status 优先于错误体 code（例如 500 即使带 `input_invalid` 也仍是 `unconfirmed`）；
  没有 status 的 code 按词表归类（`access_denied`/`forbidden` → access，`not_found` → 404，
  `revision_conflict`/`approval_conflict`/`approved_*_mismatch` → conflict，
  `validation_*` → validation）；无法归类的未知结果一律 `unconfirmed`，绝不成功。
- **409 重新复核（不重放）**：`submit()` 收到 409 或冲突类 code 时：丢弃已绑定的审批记录
  （`approval_status → required`）、清空旧预检并**自动刷新一次预检**（用当前值，刷新失败则
  显示 `preflight_state="error"`），提交状态停在 `conflict`，提交按钮
  `data-acp-submit-blocked="conflict"`，并以可读文案要求重新预检/重新审批。失败的提交
  **不会**自动重放；只有重新注入一个新的 approved 记录（`approval(record)`）才清除冲突态，
  再次提交是一次新的用户动作。`getState().submit_error` 暴露 `code` / `display_code` /
  `category` / `status` / `message`，并新增 `submit_blocked`。
- **迟到回执**：`dispose()`、切换项目/能力（generation 变化）后返回的提交结果一律丢弃
  （抛 `stale_result`），不写状态、不渲染回执。

## 6. 集成步骤（装配批次状态，2026-10-31）

1. **已由装配批次接线**（本模块未修改这些文件）：
   - `frontend/next/index.html` 已先加载 `/js/workbench/contracts/ui_subset.js`，再加载
     `/js/modules/capability-form/module.js`，并加载 `/css/modules/capability-form.css`；
   - `frontend/js/shell/layout-preferences.js` 已把 `capability-form` 放进 `EXTRA_MODULES`
     （布局编辑器可换入；模块未注册时不出现该选项）。
   若缺少 `ui_subset.js`，模块进入 typed `ui_subset_unavailable` 错误态（不会自造校验器）。
2. **默认 recipe 尚未固定**：`frontend/js/shell/recipes.js` 的默认三工作区里还没有
   `capability-form` 面板；需要时按布局评审加一个，例如：
   ```js
   { instanceId: "research-capability-form", moduleId: "capability-form",
     slot: "bottom", titleKey: "shell.module.capability_form", loading: "card",
     binding: "follow-focus" }
   ```
   （标题键 `shell.module.capability_form` 已存在于 `shell/i18n.js` 的 zh/en 两套字典；
   模块本身与槽位无关。）
3. **项目作用域**：模块不提供项目选择器。`context.projectId` 由 Shell 焦点提供
   （`task-explorer` 的项目下拉会 `setFocus({projectId})`），或在装配时调用
   `instance.setProject(...)` / 传 `deps.projectId`（无 context 时）。
4. `frontend/js/shell/i18n.js` 增加本模块的 zh/en 键（见 §7）；不补也能工作（内联 zh 兜底），
   但英文界面会缺这些文案。`next.capabilityForm.errorTitle` / `errorMessage` 供 Shell 面板
   错误态使用。
5. 审批闭环：A-57/A-58 的审批卡在拿到 approved 记录后调用 `instance.approval(record)`；
   模块把 `view_model.plan_digest` / `request.body` / `request.fields` 暴露给它做 provenance
   与摘要绑定。
6. 真实验收：需要可运行的 ACP 实例（`/api/v2/science/capability-forms` 与
   `/api/v1/jobs` 可用）与浏览器会话；vm 测试不覆盖真实 HTTP/缓存/键盘路径。

## 7. i18n 键

全部位于 `next.capabilityForm.*` 前缀（静态节点同时带 `data-i18n`，带变量的节点带
`data-i18n-key`）。分组：

| 组 | 键 |
|---|---|
| 状态 / 动作 | `state.empty` `state.no_forms` `state.loading` `state.error` `action.retry` `action.preflight` `action.preflighting` `action.submit` `action.submitting` `locked` |
| 能力 / 只读 | `capability` `tag.read_only` `tag.unavailable` `read_only` `unavailable` `version` `reason` `descriptor_digest` `form_digest` |
| 字段 | `required` `choose` `unit.second` `unit.kelvin` `unit.angstrom` `unit.hartree` |
| 审批 | `approval.required` `approval.not_approved` `approval.mismatch` `approval.granted` |
| 预检 | `preflight.idle` `preflight.loading` `preflight.ok` `preflight.stale` `preflight.error` `preflight.feasible` `preflight.infeasible` `preflight_fingerprint` `plan_digest` `problems` |
| 提交 | `submit.idle` `submit.submitting` `submit.ok` `submit.submitted` `submit.unconfirmed` `submit.unconfirmed_title` `submit.error` `submit.error_title` `submit.conflict_title` `submit.conflict` `submit.conflict_hint` |
| 错误码 | 平台/本地 code 映射：`code.ui_value_{required,type,enum,bounds,length,pattern,format,unknown_field}`、`code.ui_form_{invalid,missing,malformed,digest_mismatch}`、`code.ui_binding_{missing,unknown}`、`code.ui_subset_{unavailable,unknown_major,drift}`、`code.ui_keyword_{rejected,unknown}`、`code.platform_transport_required`、`code.capability_forms_unavailable`、`code.preflight_{required,stale,failed}`、`code.approval_{required,not_granted,card_invalid}`、`code.approved_plan_digest_mismatch`、`code.submit_unconfirmed`、`code.request_failed`、`code.input_invalid`、`code.access_denied`、`code.not_found`、`code.revision_conflict`、`code.approval_conflict`、`code.validation_failed`、`code.submit_busy`、`code.preflight_busy`、`code.stale_result`、`code.module_disposed` |
| descriptor | `errorTitle` `errorMessage` |

## 8. 已知缺口 / 未验证项

- **未做浏览器验收**：只跑了 vm 模块测试；A-59/A-60 的真实页面（脚本顺序、面板挂载、
  键盘焦点）尚未在浏览器执行。
- **审批由外部注入**：不包含审批卡/预算绑定/`plan_digest` 持久化（A-57/A-58 范围）；
  模块只消费 approved 记录并校验 `plan_digest` 一致性。**409 后不主动读取审批服务**（模块没有
  审批查询通道）：它解除本地审批绑定并刷新预检，新的 approved 记录必须由审批卡再次注入。
- **provenance 简化**：未记录经典 feature 的 `editor_ref` / `edited_at` / `human_edits`
  （proposal schema 的 provenance 段）；`view_model` 暴露 form/descriptor 摘要、body 与
  字段绑定表，供审批/提案层补齐。
- **无提交后状态跟随**：只渲染真实回执（job/attempt/status），不轮询状态、不跳转
  `task-explorer`（可由 Shell 命令后续接入）。
- **错误文案只补了内联 zh**：A-60 新增的映射码/提示（`access_denied` `not_found`
  `revision_conflict` `validation_failed` `submit_busy` `preflight_busy` 等）只在模块内联 ZH
  字典中；`frontend/js/shell/i18n.js` 的 zh/en 全键属 A-61，未在本批修改。英文界面回退到
  `CODE_TEXT` 的英文短句。
- **`unconfirmed` 不自动对账**：网络/5xx 后不轮询任务状态、不按 `idempotencyKey` 反查，只提示
  用户先查询任务状态；真实幂等对账仍需后续接线（`commands.submit` 的幂等 key 已发送）。
- **409 刷新只覆盖预检**：审批状态来自注入记录，模块无法验证服务端审批是否已撤销/过期；它只
  通过“清空绑定 + 要求重新审批”fail-closed。
- **`estimate.feasible === false` 只提示不阻断**：预检返回的不可行提示可见，但平台仍是
  提交可行性的最终权威；若产品要求硬阻断，需要契约层给“blocking”字段。
- **项目来源**：Next Shell 目前没有全局项目选择器；无 `context.projectId` 时模块停在
  `empty`，需装配层提供（见 §6.3）。
- **in-flight 请求不 abort**：generation 只丢弃迟到结果，不主动取消底层请求
  （`opts.signal` 未暴露；客户端自身支持 signal）。
- **输入锁定粒度**：预检/提交期间整张表单只读；没有逐字段 busy 态。
- **未做真实 409 / 网络故障注入**：映射与刷新路径只在 vm 假客户端上验证，未对真实 Controller
  制造 409/503/断网。
