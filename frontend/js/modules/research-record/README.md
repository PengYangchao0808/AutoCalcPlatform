# research-record（A-54 / A-55 / A-56 研究与证据面）

ACP Next 的**手工研究提问 + 科学证据（claim states）+ 平台证据投影**模块。它把三个
任务面合成一个面板，但**不合并**三种语义：问题/主张是人工输入，claim 状态是服务端
原值，证据投影是只读投影；本模块不做本地命题判定，不提交任务，不建第二套证据模型。

- 模块 id：`research-record`；注册到 `window.ACPNextModules["research-record"]`
- 经典浏览器脚本：无构建、无 ESM、除 `ACPNextModules` 外不新增全局
- 文件范围：本目录 `module.js` / `README.md`、`frontend/css/modules/research-record.css`、
  `frontend/js/module_tests/acp2_research_record.test.js`

| 任务 | 面板职责 |
|---|---|
| **A-54** 手工研究提问入口 | 人工填写 question/claim + evidence refs，然后创建 **research record**（`POST /api/v2/research/records`）或 **research proposal**（`POST /api/v2/research/proposals`）；**不需要模型**，也不调用模型网关 |
| **A-55** 科学证据 claim states | 渲染服务端 `claims[]`（或 evidence-prepare 返回的 `claim_statuses` 映射）；**验证事实**与**解释/限制**分容器；状态字符串原样显示，不判定、不合并、不升级 |
| **A-56** 平台证据投影 | `GET /api/v2/research/records/{research_id}/evidence` 只读视图：attempt/release 绑定、report digest、projection/provenance digest、claim 状态、digest-bound evidence refs；不本地计算摘要、不重算状态 |

## 文件

| 文件 | 职责 |
|---|---|
| `module.js` | descriptor + factory；四态、证据读取、手工提问表单、两个 POST body 构造、generation/dispose 守卫、纯函数 `_internals` |
| `README.md` | 本文件：契约、真实请求体、binding、i18n、接入步骤、缺口 |
| `frontend/css/modules/research-record.css` | 仅 `.acp-rr*`；颜色/圆角/控件高度取 `base.css` / `shell.css` 变量 |
| `frontend/js/module_tests/acp2_research_record.test.js` | 零依赖 vm 测试（fake DOM / fake request / fake context），沙箱没有 `fetch` |

## 模块契约

```js
ACPNextModules["research-record"] = {
  descriptor: {
    id: "research-record",
    interfaceVersion: 1,
    version: "1.0.0",
    provides: ["research.record", "research.evidence"],
    binds: ["researchRef", "taskRef"],
    capabilities: { requires: [], provides: ["research.record", "research.evidence"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.record.errorTitle", messageKey: "next.record.errorMessage" },
  },
  factory: function (deps) { /* ModuleInstance */ },
  _internals: {
    buildProposalBody, buildRecordBody, normalizeEvidence, claimRows,
    evidencePath, viewMatchesBinding, errorInfo, isDigest,
    DECISIONS, CLAIM_STATUSES, SCIENCE_FIELDS, BUDGET_FIELDS, ZH,
  },
};
```

`ModuleInstance`：`mount(hostEl, api): Promise<void>` · `updateContext(snapshot)` ·
`suspend()` · `resume()` · `dispose()` · `refresh()`（重读证据）· `snapshot()`（只读检查缝）。

## deps / binding

| 键 | 形状 | 说明 |
|---|---|---|
| `doc` | `Document` | 缺省 `document`；测试注入 fake DOM |
| `request` | `(path, opts) => Promise<body>` | **注入的 request 通道**（不是浏览器 `fetch`）；路径含 `/api/v2/...` |
| `client` | `{ transport: { request(path, opts) } }` | `deps.request` 缺失时使用；再缺省 `window.ACPClient` |
| `context` | `{ getSnapshot(), subscribe(fn) }` | 也可由 `mount(hostEl, api)` 的 `api.context` 提供 |
| `binding` | 冻结绑定对象（见下） | 首选来源；也可从 context `researchBinding` / `binding` 或 `context.binding` 读取 |
| `now` | `() => number` | 仅用于 `human_edits.edited_at` 的时钟接缝 |

**绑定（binding）是服务端事实的引用，不是本模块可编造的默认值**。缺失字段 = 可读
拒绝，绝不回退到 0/1/空字符串。绑定 JSON 也可在“高级：绑定 JSON”中显式粘贴/应用
（与宿主注入同形）。

### proposal 目标（`POST /api/v2/research/proposals`）

实际使用的 body（`src/acp/api/v2_research.py::ProposalWriteBody` +
`ResearchWriter.create_proposal` 的必需字段）：

```json
{
  "project_id": "proj-…",
  "session_seed": "…",
  "proposal": {
    "proposal_id": "prop-…",
    "project_id": "proj-…",
    "question": "<研究问题>",
    "hypothesis": "<主张/假设>",
    "question_ref": "…",            // binding 提供时
    "question_revision": 2,         // binding 提供时
    "round": 1,
    "recipe_digest": "sha256:…",
    "science": {
      "capability_steps": [{"step_index": 0, "capability": "single_point", "version": "1"}],
      "initial_inputs": [{"input_id": "in-1", "sha256": "sha256:…", "format": "xyz"}],
      "method": "r2SCAN-3c",
      "mapping": "manual-v1",
      "charge": 0,
      "multiplicity": 1,
      "allowed_substitutions": []
    },
    "budget": {
      "model_requests": 0, "input_tokens": 0, "output_tokens": 0, "task_refs": 1,
      "recipe_steps": 1, "qc_invocations": 1, "wall_seconds": 600, "queue_seconds": 0
    },
    "decision": "proposed"
  },
  "plan": { "workflow": "singlepoint", "input": {}, "method": {}, "resources": {} },
  "budget": { /* 可选：binding.requestBudget */ },
  "idempotency_key": "…"
}
```

- `question` 由“研究问题”输入，`hypothesis` 由“主张 / 假设”输入；两者在提交前都做
  非空校验（提案的 `hypothesis` 是 C 契约的必需字段）。
- `science` / `budget` / `plan` / `recipe_digest` / `proposal_id` / `round` **全部来自
  binding**，模块只做“字段是否齐全/类型是否合理”的形状校验，绝不生成科学默认值。
- **evidence refs 不属于 proposal 契约**（proposal 先于执行/证据）。表单仍接收证据
  引用用于 A-56/A-55 的上下文；proposal 目标下它们不会进入 body，UI 有明确提示。

### record 目标（`POST /api/v2/research/records`）

实际使用的 body（`RecordWriteBody` + `ResearchWriter._assemble_record`）：

```json
{
  "project_id": "proj-…",
  "session_seed": "…",
  "research_id": "res-…",                 // binding 提供时
  "proposal_ref": "prop-…",
  "approval_ref": "ap-…",
  "plan_definition_digest": "sha256:…",
  "round": 1,
  "decision": "accept|revise|reject|defer|escalate",
  "task_refs": [{
    "project_id": "proj-…",
    "job_id": "20261031_001_singlepoint",
    "attempt": 1,
    "release_id": "release-2026.10.31"
  }],
  "evidence_refs": [{
    "evidence_id": "art-…",
    "kind": "result_manifest",
    "sha256": "sha256:…",
    "relative_path": "RESULT/result_manifest.json"
  }],
  "evidence_view": { /* 当前 A-56 投影（仅当 research/round/task 绑定完全一致） */ },
  "human_edits": [
    { "edited_at": "…", "field_path": "research.question", "editor_ref": "…", "note": "<问题>" },
    { "edited_at": "…", "field_path": "research.claim", "editor_ref": "…", "note": "<主张>" }
  ]
}
```

- `task_refs` 恰好 1 条（服务端也强制 `min_length=1, max_length=1`）；`evidence_refs`
  至少 1 条且每条必须带 `sha256:…`；空白行会被丢弃。
- **研究记录契约没有 question/claim 正文字段**（`research-record.schema.json`
  `additionalProperties:false`）。人工问题/主张以 `human_edits`（溯源）形式保存，
  `editor_ref` 只取 binding 的 `editorRef` 或**服务端投影**的 `research.principal_id`；
  没有服务端主体身份时不伪造身份，`human_edits` 省略并在 UI 说明。
- `evidence_view` 只在投影与绑定（project/research/round/job/attempt/release）完全
  一致时附上；不一致时省略并显示可读提示，绝不把 foreign view 发给服务端。
- 决策是**人工选择**的研究决定（5 值冻结枚举），不是任务状态，也不是本地 claim 状态；
  未选择时拒绝提交。

## 使用的 API（全部既有，未新增路由）

**端点可用性核查（A-54 的 STOP 条件）**：两条写入路由与证据读取路由均存在于
`src/acp/api/v2_research.py`，并由 `src/acp/api/server.py` 挂载到 `/api/v2`
（`include_router(v2_research.router, prefix="/api/v2")`）。本任务**不触发 STOP**；
如果未来路由被移除，模块会以可读 error 呈现 `404/405`，而不是伪造本地记录。

| 方法/路径 | 用途 | 依赖响应字段 |
|---|---|---|
| `GET /api/v2/research/records/{research_id}/evidence?project_id=&session_seed=[&round=]` | A-56 平台证据投影（只读） | `research{principal_id,project_id,research_id,session_id,round}`、`task_ref{project_id,task_id,job_id,attempt,release{release_id,release_digest}}`、`report{report_id,digest,schema_version,subject_id}`、`execution_terminal_state`、`qualification`、`evidence_status`、`claims[]` 或 `claim_statuses{}`、`overall_claim_status`、`blocking_missing_checks[]`、`evidence_refs[]`、`limitations[]`、`provenance_digest`、`normal_reuse`、`projection_digest` |
| `POST /api/v2/research/proposals` | A-54 proposal 目标 | `{resource_id, revision, digest, document, approval, digests, ...}` |
| `POST /api/v2/research/records` | A-54 record 目标 | `{resource_id, revision, digest, document, decision, projection_digest}` |

错误形状按 `_command_error`：`{detail:{code,message,params,rule?,retryable?}}`。模块把
`status` / `code` / `rule` / `message` / `retryable` 组合成可读文案，不透出 raw stack。

## 四态与生命周期

| `data-acp-module-state` | 触发 |
|---|---|
| `empty` | 没有 research_id / 尚未读取；显示“尚未读取…”与提示 |
| `loading` | 证据读取/重试进行中 |
| `ready` | 服务端投影已通过形状归一化；渲染 digest/binding/attempt/claims/解释/refs |
| `error` | 无传输、缺 project、无 research_id、投影不完整、409/403/5xx/网络错误；显示原因 + 重试 |

- `data-acp-busy="none|submit"` 与根状态分离：提交中表单按钮 disabled、按钮文案“提交中…”，
  但已有证据仍保持 `ready`。
- `generation` 在 `mount`/`dispose` 递增；读取用 `(generation, readToken)`、提交用
  `(generation, submitToken)` 双重守卫：切换 research/选区、重新挂载或 dispose 后，
  迟到的读取/提交响应不写 DOM、不改状态。
- `dispose`：置 disposed、作废 token、退订 context、移除全部监听（含 `acp:langchange`）、
  清空 host 与 ui/evidence/binding；幂等。
- 409 提交冲突：显示 conflict 文案，随后**重读证据投影**（不重放提交）；不自动重试写入。

## 显示规则（全部服务端值）

- **验证事实**（`data-acp-section="verification"`）：execution / evidence_status /
  qualification / overall claim status 是**分开的独立行**；attempt/release 绑定与
  report/projection/provenance digest 原样显示；`claims[]` 每条显示 claim_id、status
  徽标、validator id/version、source_ref_count、blocking_missing_checks。
- **解释与限制**（`data-acp-section="explanation"`，虚线容器）：报告 `limitations[]` 与
  `blocking_missing_checks[]`；明确文案“模型解释不属于证据”。
- **证据引用**（`data-acp-section="refs"`）：evidence_id / kind / sha256 / relative_path，
  全部来自投影；没有引用时显示空态，不补造。
- **claim 状态**：`supported|refuted|inconclusive|not_evaluated` 原样显示；未知第五态
  显示原始字符串 + 警告，**绝不**映射成 supported/绿色。模块不计算
  `overall_claim_status`、不合并 claims、不产生 verdict，也不比较
  `evidence_status=="complete" && overall=="supported"`。
- `claim_statuses{}`（evidence-prepare 响应形状）是读取回退：有 `claims[]` 时优先
  `claims[]`，否则展开映射，仅显示 claim_id/status。

## 禁止项（与根 AGENTS / ANTI 对齐）

- 不调用 `/jobs*`、`/submit`、`/preflight`、`/model/*`；不提交任务，不写任务状态
  （研究决定 ≠ 任务状态）。
- 不本地计算 sha256/摘要、不本地判定/升级 claim 状态、不建第二证据模型。
- 无裸 `fetch` / XHR / 第二传输栈；无新全局（仅 `window.ACPNextModules`）。

## i18n keys（`next.record.*`）

模块内联 zh 默认值；Shell 侧 `frontend/js/shell/i18n.js` 需由 Shell 车道补 zh-CN / en-US
（本任务文件范围外）。前缀组：

```
next.record.title  subtitle  badge.readonly  badge.proposal  badge.record
next.record.section.{evidence,entry,verification,explanation,refs,binding,claims,warnings}
next.record.empty  empty_hint  no_research_id  no_project  loading  error  errorTitle  errorMessage  retry  refresh  load_evidence
next.record.source.{research_id,session_seed,round,project,hint}  no_transport
next.record.invalid_view  invalid_view_hint  no_claims  claims.note  claims.unknown
next.record.{limitations,blocking}.empty  explanation.model_note  refs.empty  refs.source
next.record.label.{research_id,project_id,round,principal,session_id,execution,evidence_status,
  qualification,overall_claim_status,normal_reuse,report_id,report_digest,report_schema,subject_id,
  projection_digest,provenance_digest,task_id,attempt,release_id,release_digest,claim_id,
  claim_status,validator,source_refs,blocking,limitations,claim_statement,binding,proposal_id,
  plan_workflow,plan_definition_digest,science,budget}
next.record.claim.{supported,refuted,inconclusive,not_evaluated,unknown}
next.record.decision.{accept,revise,reject,defer,escalate}
next.record.value.{yes,no,none}
next.record.entry.{note,question,claim,question_placeholder,claim_placeholder,target,
  target_proposal,target_record,decision,decision_placeholder,evidence,evidence_hint,
  evidence_id,kind,sha256,relative_path,add_evidence,remove,use_projection,
  use_projection_empty,binding_title,binding_hint,binding_json,apply_binding,binding_applied,
  binding_invalid,binding_not_object,submit_proposal,submit_record,submitting,
  note_proposal,note_record,note_no_binding,binding_mismatch}
next.record.entry.{question_required,claim_required,no_binding,project_required,session_required,
  proposal_id_required,proposal_id_invalid,recipe_digest_required,plan_required,
  science_required,budget_required,round_required,approval_required,plan_digest_required,
  task_ref_required,decision_required,decision_invalid,evidence_required,evidence_id_required,
  kind_required,digest_invalid,binding_not_serializable,evidence_view_refs_uncited}
next.record.notice.{proposal_created,record_created,conflict,access,not_found,failed,retryable}
next.record.warning.{claim_status_unknown,projection_digest_missing,evidence_refs_empty}
```

## 接入步骤（需要 Shell / 装配车道配合，本任务文件范围外）

1. **`frontend/next/index.html`**：在模块脚本区加
   `<script src="/js/modules/research-record/module.js"></script>`，并在 `<head>` 加
   `<link rel="stylesheet" href="/css/modules/research-record.css">`。
2. **布局注册**：`frontend/js/shell/layout-preferences.js` 的 `EXTRA_MODULES` 增加
   `"research-record": { instanceId: "research-record", moduleId: "research-record",
   titleKey: "shell.module.research_record", loading: "card" }`；或在
   `frontend/js/shell/recipes.js` 的 research workspace 加一个 panel。
3. **注入绑定（关键）**：Shell 的 `layout-manager.hostApi()` 目前只传
   `context/commands/panel`，没有 per-module `deps.binding` 通道。两条接入路径：
   - 由 Shell 在 `factory(host)` 前包装/扩展：把 `researchBinding` 放进 context
     snapshot（模块会读 `snapshot.researchBinding` / `snapshot.binding` /
     `context.binding`）；
   - 或由宿主提供 `deps.binding`（测试/独立面板直接用）。
   绑定应来自已存在的提案/审批/任务上下文（例如 approval-card / task-config /
   agent-workspace 的选中项），**不要**由本模块猜测。
4. **Shell i18n（推荐）**：把 `next.record.*` 写入 `shell/i18n.js` 的 zh-CN/en-US；
   未写入时模块显示内联 zh 文案（不阻塞）。
5. **选择联动（可选）**：宿主把选中的 `researchRef.recordId` 放入 context，模块即可
   自动读取对应证据；也可在面板内手动输入 research_id/session_seed/round。

## 测试

```bash
node frontend/js/module_tests/run_all.js acp2_research_record
```

覆盖：注册/descriptor、源码静态门（无 fetch/jobs/crypto/本地 verdict）、proposal body
（question/hypothesis/question_ref/science/budget/plan/idempotency、无 evidence refs）、
record body（task_refs/evidence_refs/evidence_view/human_edits、无 question/hypothesis）、
两个 body 的缺失字段拒绝、证据归一化（claims/claim_statuses/未知状态/不完整投影）、
证据路由编码、empty/loading/ready/error+retry、无传输、投影字段（digest/binding/attempt）、
claim 原值展示、验证/解释分容器、未知第五态不升级、生成守卫、dispose、proposal POST
回执、busy 防重、record POST（含投影引用/原始 evidence_view/时间戳 human_edits）、409
冲突刷新不重放、403 可读、binding JSON 应用/拒绝、i18n 键、CSS 作用域、README 契约。

## 已知缺口 / 不确定项

- **未改** `frontend/next/index.html` / `shell/layout-preferences.js` / `shell/recipes.js` /
  `shell/i18n.js`（文件所有权限制）；接入需上面步骤，在此之前模块不会被页面加载，
  也无法做真实浏览器验收。
- **Shell binding 通道缺失**：`layout-manager.hostApi()` 没有 per-module deps；需要
  Shell 车道把 `researchBinding` 放进 context 或提供 factory 包装。本模块提供了
  “高级：绑定 JSON”作为独立演示/测试兜底，但生产应由宿主注入。
- **proposal 契约没有 evidence refs**：A-54 的“问题/主张 + 证据引用”在 proposal 目标下
  只能把证据引用当上下文（A-56/A-55 使用）；真正带证据判定的是 record 目标。这是
  `proposal.schema.json` 的冻结边界，不是模块偷懒。
- **proposal 读回契约的完整字段未全带**：模块发送的是平台 writer 实际接受的冻结子集
  （question/hypothesis/recipe_digest/science/budget/plan 等），未包含 C 读契约要求的
  `reaction_ref`/`reaction_revision`/`allowed_change_scope`/`approval_ref`/
  `plan_definition_digest`/`created_at` 等（这些需要真实反应/计划/审批上下文）。平台写入
  不校验 C 全 schema；但若用 `ACPClient.research.fetchProposal` 的冻结 validator 读回，
  可能判为不完整。完整闭环应由 task-config / capability-form / RPH 提供完整 proposal
  binding 后再写入。
- **record 契约没有 question/claim 字段**：人工提问以 `human_edits` 溯源形式保存，
  需要 binding 的 `editorRef` 或服务端投影的 `principal_id`；两者都没有时正文不会
  上传（UI 明示），不会伪造 provenance。
- **proposal record 绑定字段由宿主提供**：science/budget/plan/approval/plan_definition_digest
  不在本模块生成；真实闭环需 A-54 上游（task-config / capability-form / RPH）产出 binding。
- **未做 append round**：模块只调用 `POST /research/records`（create）；`POST
  /research/records/{id}/rounds`（CAS append）尚未接入（需要 `expected_revision` 与
  读取已有 record）。A-54 的“新建”范围不要求 append。
- **未真实联调**：本任务仅 Node vm 模块测试；未启动 Controller/真实 HTTP，未验证真实
  `/research/records` 的审批/证据链；后端语义以 `src/acp/api/v2_research.py` 与
  `acp_services/runtime/{research_writer,evidence_projection}.py` 为准。
- **claim_statuses 回退**：真实 A-56 GET 返回 `claims[]`；`claim_statuses{}` 是
  evidence-prepare 响应形状，模块只做**投影内**读取回退，不主动调用
  `POST /research/evidence/prepare`；prepare 响应本身（`{report_ref, claim_statuses}`）
  尚未接入（A-55 以 GET 投影为准，避免再出发明/持久化路径）。
