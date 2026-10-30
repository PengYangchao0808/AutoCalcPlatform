# agent-workspace（UI-03A 只读首片）

ACP Next 实验入口的 Agent Workspace 面板模块（research 工作区 main 槽，`instanceId: research-agent`）。
经典 script（无构建 / 无 ESM），注册到 `window.ACPNextModules["agent-workspace"]`。

**首片只读**：本片**没有任何写操作与审批按钮**。卡片上的全部按钮只做导航（`openTask` / `openStructure` / `openEvidence` / 卡内重试）；审批、提交、编辑等写动作属于后续阶段的统一入口，禁止在本模块内出现。

设计规格：`docs/reference/next-change/ACP_Frontend_UI01_Shell_Design_20261030.md` §4（三卡骨架、四态节点、模型解释 vs 验证事实强制二分、连接/缺失态）与 §6（`agent.*` i18n key）。

## 文件

| 文件 | 职责 |
|---|---|
| `module.js` | 模块本体：descriptor + factory；三卡渲染、generation 守卫、fixture 缝隙 |
| `README.md` | 本文件：冻结契约、数据映射、已知缺口 |
| `../../css/shell/agent.css` | 本模块全部样式（`acp-card-*` / `acp-timeline-*` / `acp-tag-*` / 二分容器），由 Shell 车道的 `next/index.html` 静态 `<link>` 加载 |
| `../../module_tests/acp2_module_agent.test.js` | 零依赖 vm 测试（mock client、fetch 禁网） |

## 模块契约（与 Shell 对接）

```js
ACPNextModules["agent-workspace"] = {
  descriptor: {
    id: "agent-workspace", interfaceVersion: 1, dependencies: [],
    capabilities: { requires: [], provides: ["research.summary","research.plan","research.evidence"] },
    hosts: ["acp-web"], supportedInstancing: "single", configSchemaVersion: 0,
    error: { titleKey: "agent.workspace.errorTitle", messageKey: "agent.workspace.errorMessage" },
  },
  factory: function (deps) { /* ModuleInstance */ },
};
```

`deps`：

| 键 | 形状 | 说明 |
|---|---|---|
| `context` | `{ getSnapshot(): ContextSnapshot, subscribe(fn): unsubscribe }` | Shell 上下文源；`subscribe` 可选 |
| `commands` | `{ openTask(taskRef), openStructure(structureRef), openEvidence(taskRef) }` | 导航命令；缺失时按钮静默不动作 |
| `client` | `ACPClient` 形状或 mock | 缺省 `window.ACPClient`；测试注入 mock |

`ModuleInstance`：`mount(host, initial: ContextSnapshot): Promise<void>` · `updateContext(next)` · `dispose()`。
（`resize`/`suspend`/`resume` 未实现 —— 本片无画布无轮询。）

**dispose 幂等彻底**：置 `disposed`、`generation+1`（作废一切在途响应）、退订 context、移除委托 click 监听、清空 `host.innerHTML`、置 `host=null`。重复调用无副作用；dispose 后迟到的响应被丢弃不写入。

## 冻结形状（v1，变更需改本文件 + 测试）

### ContextSnapshot

```js
{
  projectId: string | null,      // 必填于真实取数；缺失 → 各卡错误态（不臆造 scope）
  permission: string,            // 默认 "read"，透传 client scope
  researchRef: { recordId: string } | { proposalId: string } | null,
  taskRef: { jobId: string, attempt: number } | null,   // attempt 缺省按 1
}
```

- `researchRef` 首版**只支持二选一**：`recordId`（ResearchRecord，走 `fetchRecord`）或 `proposalId`（Proposal，走 `fetchProposal`）。两者同时给出时 record 优先。
- `taskRef` 驱动时间线「实际执行」段与证据卡；缺失时两处各自显式空态，摘要卡照常渲染。

### updateContext 刷新语义（token + generation 防串台）

| 变更 | 刷新 |
|---|---|
| `researchRef` 变（含 A→B→A 往返） | 摘要卡 + 计划卡候选段重载；`generation+1`，旧响应全部作废 |
| `taskRef` 变（含 rerun attempt 变化） | 时间线执行段 + 证据卡重载；同上 |
| 仅 `projectId`/`permission` 变 | 重渲染当前快照（不自动重取；下一次显式刷新生效） |

### fixture 缝隙（仅测试/演示）

`window.__ACP_NEXT_AGENT_FIXTURE__` 存在时替代全部网络取数：

```js
{
  record:   ResearchRecord | null,   // researchRef.recordId 时用
  proposal: Proposal       | null,   // researchRef.proposalId 时用
  taskStatus: TaskStatus   | null,   // taskRef 存在时用
  view:     ViewModel      | null,   // 仍经 validateViewModel 校验，不合格照旧错误态
  modelNote: string        | optional, // 演示模型解释容器（真实路径永不来自 fixture）
}
```

**fixture 与真实数据标识必须分开**：fixture 生效时根节点 `data-acp-fixture="true"` + 可见 amber 徽章「演示数据（fixture）」。默认（无 fixture）走 client 只读 API；client 不可用 → 各卡显式错误态；无 `researchRef`/`taskRef` → 面板显式空态。**任何路径都不显示臆造数据。**

## 三卡数据映射（字段 → 来源 API）

### 1. 研究摘要卡 `acp-card-summary`

| 显示字段 | 数据来源 | 缺失表现 |
|---|---|---|
| 研究目标 | `proposal.hypothesis`（仅 proposal 有此字段） | record → `—`（不从别处推断） |
| 输入结构 | `proposal.science.initial_inputs[].input_id` | record / 空数组 → 「无」 |
| 记录版本 | `record.record_revision` → `record r{n}` | proposal → `—` |
| 决策 | `summarizeRecord/summarizeProposal().decision`（冻结枚举 `RECORD_DECISIONS`/`PROPOSAL_DECISIONS` 原样显示） | `—` |
| 审批引用 | `summarize*().approval_ref` | null → `—`（绝不升级为已审批） |
| 计划摘要 | `proposal.plan_definition_digest` 或 `record.plan_definition_digest` | `—` |
| 关联任务 | `record.task_refs[]`（chip）；状态 badge 仅来自 `science.fetchTaskStatus` 的服务权威状态 | 「无」 |
| 卡 meta | record：`research_id`；proposal：`proposal_id · round n` | — |

取数：`research.fetchRecord(ref, scope)` / `research.fetchProposal(ref, scope)` + `research.summarizeRecord/summarizeProposal`（client 无 summarizer 时用模块内同形本地投影）。
按钮：`打开任务`（`commands.openTask({jobId, attempt})`，来自 `task_refs[0]` 或 `taskRef`）、`打开结构`（仅当证据卡视图中存在 `availability=readable` 且路径为结构格式的 artifact 时可用；否则 disabled + title 说明，不隐藏）。

### 2. 计划/步骤时间线 `acp-card-plan`

候选段（来自 research 文档）：

| decision | 节点类 | tag |
|---|---|---|
| `proposed`（无 approval_ref） | `is-candidate` 空心 | `acp-tag--candidate` 候选 |
| `proposed`（带 approval_ref，服务侧审批中） | `is-pending` 虚线空心 | `acp-tag--pending` 待审批 |
| `deferred` / record `defer`/`escalate` | `is-pending` | `acp-tag--pending` |
| `approved` | `is-submitted` 实心 accent+halo | `acp-tag--submitted` 已提交 |
| `rejected` / record `reject` | `is-result` `data-tone="fail"` | `acp-tag--result` |
| `superseded` / record `revise` | `is-result` `data-tone="warn"` | `acp-tag--result` |
| record `accept` | `is-result` `data-tone="pass"` | `acp-tag--result` |

执行段（来自 `context.taskRef` → `science.fetchTaskStatus`，服务权威状态原样显示）：

| status | 节点类 |
|---|---|
| `QUEUED`/`RUNNING`/`PAUSED` | `is-submitted` |
| `WAITING_REVIEW` | `is-pending` |
| `COMPLETED`/`FAILED`/`CANCELLED` | `is-result`（pass/fail/warn tone） |
| 未知名次 | `is-submitted` + 原始 status 文本（不推断） |

**候选计划与实际执行分开两段**（`agent.plan.section.candidate` / `agent.plan.section.execution`）。
**工具调用时间线本片不做**：`data-acp-tools-placeholder="true"` 的 disabled 占位条 + 文案「工具调用时间线将在 DSH 接入后提供」，**不显示任何假数据**。

### 3. 证据卡 `acp-card-evidence`

| 显示字段 | 数据来源 |
|---|---|
| 来源 | `view.task_ref` + `view.provenance.source` |
| 版本 | `view.manifest_generation` + `view.provenance.digest` |
| 验证报告 | 全部 claim 合成：任一 `refuted`→fail；有 `inconclusive`/`not_evaluated`→warn；全 `supported`→pass（shield 标记） |
| 缺失项 | `view.artifacts[]` 中 `availability != "readable"` 的条目（`acp-tag--missing` + `reason` title） |
| 验证结论列表 | `view.claims[]` 四态逐条：`supported`→`acp-verified-fact is-pass`、`refuted`→`is-fail`、`inconclusive`→`is-warn`、`not_evaluated`→普通缺失行（**绝不** pass/green、不进 verified-fact 容器） |
| 打开结构 | 仅当存在 `readable` 结构 artifact（`.xyz/.sdf/.mol/.pdb/.cif/.genpos`）→ `commands.openStructure({jobId, attempt, artifactId, relativePath})` |
| 查看报告/工件 | `commands.openEvidence({jobId, attempt})` |

取数：`science.fetchView(taskRef, scope)`，**渲染前必须通过 `contracts.experience.validateViewModel`**；校验失败（含 validator 不可用）→ 显式「数据不合规」错误态 + 重试，**绝不半渲染**。

### 模型解释 vs 科学验证（强制二分，§4.5）

- 模型自由文本只进 `.acp-model-note`（虚线灰容器 + `acp-tag--model` + spark 图标），本片仅 fixture 的 `modelNote` 可产生（真实路径待 DSH 接入）。
- 验证结论只进 `.acp-verified-fact is-pass|is-fail|is-warn`（语义色左边条 + shield 图标）或缺失行，数据只来自服务权威 view/claims。
- 模型容器内禁止出现 green/red/amber 任何形式（测试守护）；工具调用成功 ≠ 任务完成 ≠ 验证通过，三者独立呈现。

### 连接状态语义

无 DSH/模型接入是**正常态**：面板顶部常驻 `acp-tag--conn` 徽章「只读 · 未连接模型」（`acp-tag--model` 语义保留给模型解释注记专用），不显示任何连接进度假象、不显示会话/工具卡。

## i18n

所有文案经 `data-i18n` + `agent.*` key（zh 字面量内联为默认）。Shell 的 `window.ACPShell.i18n.t` 存在时挂载后重扫应用；不存在时直显 zh —— **不硬依赖 Shell i18n**。key 清单见设计规格 §6 + 本模块新增：`agent.plan.section.candidate/execution`、`agent.plan.empty_task`、`agent.plan.tools.pending`、`agent.evidence.claims`、`agent.verify.not_evaluated`、`agent.summary.decision/approval_ref/plan_digest`、`agent.fixture.badge`、`agent.error.load_failed/no_client/scope_missing/invalid_data`、`agent.action.retry`、`agent.workspace.errorTitle/errorMessage`。

## 测试

```bash
node frontend/js/module_tests/run_all.js acp2_module_agent   # 11 tests
node frontend/js/module_tests/run_all.js                     # 全量回归
```

覆盖：契约 descriptor、空态、摘要字段、四态时间线映射、taskRef 更新 + 迟到响应作废、validateViewModel 拒绝、not_evaluated 不渲染为通过、二分 DOM class 断言、dispose 幂等无残留、fixture 缝隙（含 demo 徽章）、DOM 内无写命令。

## 已知缺口（UI-03B+）

1. **工具调用时间线**：占位条已就位（`acp-card-tools` 类名保留），DSH 接入后实现。
2. **审批卡** `acp-card-approval`：类名保留未实现；审批动作属后续统一入口。
3. **模型解释真实数据源**：当前仅 fixture 可注入 `modelNote`；DSH/宿主事件接入后接真实会话文字（仍必须包 `acp-model-note`）。
4. **连接状态未知**（`acp-tag--warn`「连接状态未知」）：需 DSH 事件/轮询数据源，本片只有「未连接模型」正常态。
5. **摘要卡「打开结构」**：依赖证据卡视图中的结构 artifact；proposal-only 上下文（无 taskRef）时恒 disabled——待结构来源索引接入后从 `initial_inputs` 直接解析。
6. **Shell i18n 语言切换热刷**：`acp:langchange` 事件监听未挂（Shell 侧事件尚未定稿）；当前在每次 render 时取词。
