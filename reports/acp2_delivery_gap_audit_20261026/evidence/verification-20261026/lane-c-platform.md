# Lane C 验证记录 — 前端审批 / 平台审批 / 预算 / 研究数据 / 科学 UI

- **验证对象**：`reports/acp2_delivery_gap_audit_20261026/GAP_REPORT.md` 第 220–257 行（G11–G14）与 268–275 行（G16）
- **HEAD**：`ec4a0b3`（2026-10-25 04:19:08 +0800，`fix(acp2): final acceptance remediation — read-only science/research routes + defect fixes`）
- **方法**：只读源码、只读 git 历史、`/usr/bin/python3.11` 纯函数探针；未修改业务代码、未 git commit、未重启服务、未提交 QC、未调用模型 API。
- **总览**

| 项 | 结论 | 一句话 |
|---|---|---|
| G11 | **确认** | `research-approval.js` 是本地 fixture 状态机，全前端无审批写传输；"B16 not delivered" 是 B16 合并前写下的注释，之后未回收。 |
| G12 | **确认** | `_is_research_launch`（含 budget）与 `_enforce_approval`（不含 budget）字段集不同；仅 budget 的真实 helper 探针返回 launch=true、approval=None；MCP/身份层没有"Agent 必须审批"策略。 |
| G13 | **确认（需修正表述）** | 三套预算词表确实分裂；探针显示 `core_hours`/`walltime_s` 会在 policy 解析处抛错，RPH 8 字段 PlanBudget 被静默解析成 `limits={}`；且 production 结算只写 task_ref/attempt，recorded-only 维度无自动 producer。 |
| G14 | **确认** | `ResearchResourceStore.create/compare_and_swap` 存在但只被 acceptance/测试直接调用；生产只有只读 GET；P2 写入工具在 MCP 端完全缺失。 |
| G16 | **确认** | `question_specs` 硬编码 3 工作流，前端固定 6 字段；计划中的 `capability-form.js` 从未实现（已有缺陷报告记录）。 |

---

## G11：Workbench 审批模块制造本地成功状态

### 验证结论：**确认**

报告的核心事实成立：`frontend/js/workbench/features/research-approval.js` 是一个纯本地 fixture 审批状态机，`src/acp/api/v2_approvals.py` 的持久审批 HTTP 端点存在，但两者之间没有任何 transport 适配。

### 报告原声明不准确 / 需补充处

1. **行号有偏移**：
   - fixture lifecycle 注释实际在 `research-approval.js:524–526`（`// platform research approval lifecycle (fixture record; B16 not delivered)`），不是 `:522`；`:522` 是 `assertUserPrincipal` 的右花括号。
   - `apr-*` 生成在 `research-approval.js:584`，不是 `:555`；`:555` 是 `requestApproval` 的文档注释首行。
   - `submitted: true` 在 `:699`，`dispatch_ref: "dsp-*"` 在 `:700`（`:698–705` 整个返回对象），报告写 `:699` 只覆盖一半。
2. **不应把 stale 注释写成作者当时的错误**：该模块 `e03a9dd` 提交于 **2026-10-23 07:31:28**，B16 的 `v2_approvals.py` 提交 `e5463de` 于 **同日 13:05:12**。模块写"B16 not delivered in this run"在写作时是事实；问题是 B16 合并后没有回收该注释与接线（详见根因链）。
3. **默认页面不会自动出现虚假卡片**：`ACP_Workbench_v2.html:354–359` 确实有 `#research-approval-body`，模块 `:1075–1079` 在 DOMContentLoaded 时自动 `mount`，但全仓没有生产代码调用 `ACPResearchApproval.mount({proposal, cardRef})` 或注入 `proposalRef`（`grep -rn ACPResearchApproval frontend/` 只命中模块自身与 `module_tests/acp2_approval.test.js`）。因此默认页面是空卡片；虚假 `approved/submitted` 只在测试/探针或未来调用者注入数据时出现。报告已声明"不代表已观察到真实浏览器用户点击"，建议把这个"默认空态"写清楚。
4. **持久化也不存在**：`persist()`（`:532–539`）只在 `deps.persistence.write` 存在时落盘；全仓没有任何地方给该模块注入 `persistence`（`grep -rn persistence frontend/js/workbench/bootstrap.js frontend/ACP_Workbench_v2.html` 无命中）。所以卡片连刷新都不能存活，"跨端恢复"不只是缺服务器，连本地持久化都没有接线。

### 证据

**本地生命周期与假回执**
- `frontend/js/workbench/features/research-approval.js:10–11`：`Contract rules enforced here (fixture mode; the Runtime approval endpoint B16 is not delivered in this run)`。
- `:23–25`：`approval transitions are pure functions over the card record. This module owns no network call of its own.`
- `:532–539` `persist()`：写内存 `cards` + 可选 `deps.persistence.write`。
- `:556–601` `requestApproval()`：本地生成 `approval_ref: "apr-" + seq`（`:584`）、`status: PENDING`，无服务器往返。
- `:612–637` `decideApproval()`：依据传入 `action.principal` 在客户端判定 `approver_mismatch` 并改状态。
- `:668–706` `submitPlan()`：本地校验通过后直接返回 `{submitted: true, dispatch_ref: "dsp-" + seq, ...}`（`:698–705`），无任何 transport。

**前端加载与真实服务端**
- `frontend/ACP_Workbench_v2.html:1288`：`<script src="js/workbench/features/research-approval.js"></script>`；面板容器在 `:354–359`。
- `src/acp/api/v2_approvals.py:1–16` 文档声明 B16 持久审批；端点：`POST /research/approvals`（`:251`）、`GET /research/approvals`（`:291`）、`GET /research/approvals/{approval_ref}`（`:340`）、approve/reject/revoke（`:424/431/438`）。身份来自 Controller 通道（`:111` `_principal`，`:155` `_require_submit_access`），拒绝 payload 里的 approver 字段（`:119` `_reject_payload_identity`）。

**全前端无写传输**
- `frontend/js/client/research-query.js:263–276`：`ACPClient.research` 公开面只有 `fetchProposal` / `fetchRecord` / `readRound` / `summarize*` / `invalidate*`，并显式 `readOnly: true`；无 request/approve/reject/revoke/submit 方法。
- `frontend/js/workbench/compat/api.js:41` 只执行 `ACPClient.research.init({ v2: apiV2 })`，即只接只读客户端。
- `grep -rn "research/approvals" frontend/` 无命中；`grep -rn "fetch\|XMLHttpRequest\|transport" frontend/js/workbench/features/research-approval.js` 只在 `:1019` 命中 `deps.research.fetchProposal`（只读 proposal），无审批写调用。

**验收脚本把 fixture 固化为通过条件**
- `scripts/acceptance/acp2_experience_scenario_approval_budget_live.py:70–75` 的 `FORBIDDEN_PATTERNS` 明确把 `fetch(`、`XMLHttpRequest`、viewer、`child_process` 列为该模块禁止项；`:167–172` 以 `approval.fixture_content_no_platform_transport` 作为断言，即"没有平台传输"是 PASS 条件。
- 同文件 `:130` 仍写 `"b16_handoff: Runtime budget endpoint (B16/B17) is not delivered"`（该文本在 B17 `v2_budgets.py` 落地后已 stale，见 G13）。

### 根因链

**技术根因**
1. 审批模块只有状态机与 DOM 渲染；写入动作没有 transport 实现。读路径有 `ACPClient.research`（只读），写路径既不在 feature 内（验收禁止），也不在 client 层（`research-query.js` `readOnly: true`）。
2. 没有持久化端口：`persistence` 可选但从未注入，卡片仅存 `cards` 内存对象，重载即失。
3. 服务端 B16 的持久审批是 HTTP `/api/v2/research/approvals`；C 轨研究客户端契约选的是 MCP P2 工具（`rph/packages/bridge/src/index.ts:537–570`），而 MCP 端没有这些工具（G02），因此 B16 落地后 C 侧仍没有可用写通道。

**设计/流程根因**
1. **并行计划时序**：C19a 的验收脚本与模块都在 B16 之前完成（`e03a9dd` 07:31 → `003b625` 09:41 → `052962e` 09:59 → `39d4aeb` 10:45 → B16 `e5463de` 13:05）。C19 计划前置条件写的是"B 已交付审批/quote/admit/reject…真实端点"（`docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md:346`），但实际执行先做了 fixture。
2. **验收口径把 fixture 变成终点**：`acp2_experience_scenario_approval_budget_live.py` 的 `approval` phase 明确"不启动 Controller/DSH/model/QC/LSF/浏览器"（`:6–11`），并以"无传输 + fixture 标注"为 PASS。于是"未接线"不会被任何门判失败。
3. **角色边界**：C19 的证据交接写明"由 B 提供权威 dispatch/账本/审批记录，C 只验证用户/模型可见反馈"（同计划 `:351`）。B16 交付 HTTP 后，没有任务把"C 的可见反馈"从 fixture 切到真实端点；10-24 的集成 merge（`bf5ee86`）也没有重开 C19a。
4. **注释未回收**：`e03a9dd` 写入的 `"B16 is not delivered in this run"` 在 `e5463de` 之后未更新，`052962e`/`39d4aeb` 只做 fail-close 与 i18n，没有触碰 transport 或注释。

---

## G12：审批强制策略由客户端字段选择，budget-only 路径还存在内部分歧

### 验证结论：**确认**

### 报告原声明不准确 / 需补充处

1. MCP schema 的原文是"省略 **all four**"（`plan_digest` + `proposal_ref` + `proposal_digest` + `budget`），报告写成"省略 plan/proposal/budget"漏了 `proposal_digest`（`src/acp_services/entrypoints/mcp.py:300–306`）。
2. 报告说 schema"明确告诉调用方…走 legacy"；更准确的表述是 schema 同时写明 **"the field relations below are documented for callers, never enforced here — the Controller remains the gate"**（`mcp.py:299–301`）。即服务端自己声明这层关系不校验，问题不只是"文档提示"，而是**入口明确不做策略**。
3. 报告说"随后 `_budget_claim_from_approval(None)` 不产生研究预算 claim"；补充：`req.budget` 在 `v1_routes.py` 中唯一出现的位置就是 `:8387` 传给 `authorize_submission(budget=req.budget)`，之后再无消费点。因此 budget-only 不只是"没有 approval"，其 budget 值在通过 `_enforce_approval` 后被**完全丢弃**。
4. 还有一个报告未提的非对称：budget-only 时 `_is_research_launch` 为真，`:2561–2562` 会先走 `_research_project` 的严格项目校验；即同一次请求"项目范围按研究处理、审批与预算按 legacy 处理"。

### 证据

**代码路径**
- `src/acp/api/v1_routes.py:8436–8442`：
  ```python
  def _is_research_launch(req: Any) -> bool:
      return bool(getattr(req, "proposal_ref", None) or getattr(req, "budget", None)
                  or getattr(req, "proposal_digest", None) or getattr(req, "plan_digest", None))
  ```
- `src/acp/api/v1_routes.py:8338–8339`：
  ```python
  proposal_bound = bool(req.proposal_ref or req.proposal_digest or req.plan_digest)
  if not proposal_bound:
      return None
  ```
  **不含 `budget`**。
- 调用顺序：`v1_routes.py:2561–2562`（`if _is_research_launch(req): req.project_id = _research_project(...)`）→ `:2566`（`approval = _enforce_approval(request, req)`）→ `:2644`（`budget_claim = _budget_claim_from_approval(approval)`）→ `:2647/2650` 传给 `submit(spec, budget=budget_claim)`。
- MCP 描述：`src/acp_services/entrypoints/mcp.py:296–306`、`:323–327`（"Omit it, together with proposal_ref/proposal_digest/budget, for a legacy launch."）。
- MCP 授权只有 action 级：`src/acp_services/entrypoints/mcp.py:1132–1139`，`TOOL_REQUIRED_ACTION[SUBMIT] = WRITE`（`:258`）。没有审批策略。
- 身份词表没有 agent/model：`src/acp_services/contracts/access.py:88–94` 只有 `user/service/anonymous`；`mcp.py:16–17` 说明 principal 来自已验证本地主体。平台在身份层无法区分"Agent 提交"与"人类提交"。
- `action_gating` 只用于 init wizard 外部动作（`src/acp/init_wizard/gating.py:4–30`），未进入 submit/审批路径；`entry_authz` 是资源 entry class（`src/acp_services/workspace/entry_authz.py:80–99`：download/log/range/frame_candidates/structure_sources/cache_read/event_stream），不是 agent 策略。

**复现（纯 helper，未提交任务）**

`/usr/bin/python3.11` 探针（`/tmp/opencode/lane_c_g12_probe.py`，仅 import 与构造 `SimpleNamespace`）：

```
budget_only is_research_launch= True
omit_all is_research_launch= False
plan_only is_research_launch= True
proposal_ref_only is_research_launch= True
budget_claim(None)= None
enforce_approval(budget_only)= None
```

即：仅 budget 的请求被标记为研究入口，但审批检查直接返回 `None`，研究预算 claim 也为 `None`。与报告一致。未提交真实任务，故不声称 quota 被绕过。

### 根因链

**技术根因**
1. 存在两个独立维护的"研究入口"判定：
   - `_is_research_launch` 由 `f67f90f`（2026-10-22 22:51，`feat(mcp): bind mcp tools to the single controller application surface`）引入，包含 `budget`；
   - `_enforce_approval` 由 `e5463de`（2026-10-23 13:05，B16）引入，用另一个字段集 `proposal_ref/proposal_digest/plan_digest`，不含 `budget`。
   两者没有共享 helper（如 `research_binding_fields()`），也没有测试把两个判定对齐。
2. `_enforce_approval` 用"三个绑定字段任一存在"作为 opt-in；budget 不在其中，因此 budget-only 提前返回 `None`，后续 `_budget_claim_from_approval(None)` 返回 `None`，`req.budget` 不再被使用。
3. 没有任何入口级策略：MCP `_authorize` 只检查 `WRITE`；`PrincipalKind` 没有 agent 类；`acp_submit` 的 research 字段全部 optional。服务端无法回答"这个 credential 是否必须带审批"。

**设计/流程根因**
1. 设计把"研究提交"定义成一个**payload 开关**，而不是入口身份/授权范围。计划 B 的 P2 表虽然写"approval tool requires a server-verified human approver"（`docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json` `approval_lifecycle`），但 P1 `acp_submit` 仍保留 legacy 兼容，且 schema 明示"relations never enforced here"。
2. B16 的审批 gate 只服务"已经主动声明自己研究"的客户端；legacy 兼容被当作默认逃生门。没有任何测试覆盖"Agent 省略全部字段"或"budget-only"的入口反例（报告关闭条件也指出了这一点）。
3. 结果：修复 budget-only 的字段对齐只能消除内部矛盾，不能实现"Agent 必须审批"；后者需要入口策略（身份/credential scope/服务端必填），当前 schema 与身份模型都没有承载点。

---

## G13：预算表示与执行能力分裂

### 验证结论：**确认（报告表述需修正两处，且实际分裂比报告描述更深）**

### 报告原声明不准确 / 需补充处

1. **"三套预算词表…无统一换算契约"部分不准确**：存在一个不完整的 alias 映射。`src/acp_services/runtime/usage_units.py:37` 把 `task_count` 映射为 `task_ref`，`parse_budget_policy`（`research_budget.py:346–396`）能接受 `{"unit":"task_count","count":N}` 并转成 ledger 的 `task_ref`。所以准确说法是"**只有 task_count→task_ref 的 ad-hoc 映射，没有完整换算/校验契约**"。
2. **"recipe_step/QC/bsub/model_request 为 recorded-only"在 production 被高估**：能力表（`research_budget.py:134–140`）这样标注，但 production 唯一的结算调用 `local_admission.py:671–680` 只提交 `task_ref=1, attempt=N`；`recipe_step/qc_invocation/bsub/model_request` 没有自动 producer。唯一能写这些计数的是 `POST /api/v2/research/budgets/{id}/settle`（`src/acp/api/v2_budgets.py:98–140`），而全仓消费者只有 `scripts/acceptance/acp2_runtime.py:10902/10911`。因此对这些维度，实际能力低于 "recorded-only"。
3. **model-token 的标注自相矛盾**：`UNIT_CAPABILITIES[MODEL_REQUEST] = RECORDED_ONLY`（`:140`），但 `release_statement()` 把 `model_token_enforcement` 放进 `not_verified`（`:600–607`），文档又写 NOT_VERIFIED（`:19–20`、`:265–268`）。报告只引用了 NOT_VERIFIED，未指出同一文件里的 recorded-only 声明。
4. **报告完全未提 `v2_budgets.py`**：B17 已有 GET projection/evidence + POST settle（`src/acp/api/v2_budgets.py:67/81/98`，server 注册于 `src/acp/api/server.py:436`）。这使 C 验收脚本 `acp2_experience_scenario_approval_budget_live.py:130` 的 "Runtime budget endpoint (B16/B17) is not delivered" 成为 stale 声明。
5. 行号：`application_params.py:142` 是函数 `_normalize_budget` 起始；unit 枚举定义在 `:38`，校验在 `:147–150`。`research_budget.py:134` 精确命中 `UNIT_CAPABILITIES`，正确。

### 证据

**四套预算表示**

| 表示 | 位置 | 字段/单位 | 消费方 |
|---|---|---|---|
| RPH `PlanBudget`（C） | `rph/packages/dsh-bundle/skills/models.ts:53–61`，校验 `:123–130` | `model_requests/input_tokens/output_tokens/task_refs/recipe_steps/qc_invocations/wall_seconds/queue_seconds` | Supervisor 内存估算 `supervisor.ts:372/410/579–586`；proposal digest |
| 应用 submit budget（B03） | `src/acp_services/entrypoints/application_params.py:38`，`:142–163` | `unit ∈ {task_count, core_hours, walltime_s}` + `timeout_s` + optional `count` | 只作为 approval binding 的 digest 输入（`v1_routes.py:8387` → `approvals.py:876–880`） |
| B17 research ledger | `src/acp_services/runtime/research_budget.py:134–140` | `task_ref/attempt/recipe_step/qc_invocation/bsub/model_request` | 生产只 reserve/settle task_ref+attempt（`local_admission.py:521–545,671–680`） |
| C 模型预算代理 | `scripts/acceptance/acp2_experience_budget_proxy.py:1–30`，docs `experience/model-budget.md:9–13` | `model_requests/input_tokens/output_tokens`，独立 ledger `$ACP2_DATA_ROOT/budget/ledger.json` | 仅 acceptance harness，RPH 包不发行 |

**关键探针**（`/tmp/opencode/lane_c_g13_probe.py`，直接调用 `parse_budget_policy`）：

```
app_task_count  OK limits= {'task_ref': 2} timeouts= {'science_runtime_s': 3600.0, ...}
app_core_hours  ERROR UsageUnitValidationError unknown usage unit 'core_hours'
app_walltime_s  ERROR UsageUnitValidationError unknown usage unit 'walltime_s'
rph_plan_budget OK limits= {}  timeouts= {'science_runtime_s': 1800.0, ...}
```

含义：
- 应用契约允许的 `core_hours` / `walltime_s` 进入 B17 policy 解析会抛 `UsageUnitValidationError`。`local_admission.py:334–355` 只捕获 `BudgetConfigurationError`，而 `UsageUnitValidationError` 是另一个 `ValueError` 子类；若 approval 记录里带这两个 unit，submit 路径存在未捕获异常风险（**静态推导 + 单元探针，未提交真实任务，未确认 HTTP 状态码**）。
- RPH 的 8 字段 `PlanBudget` 传给 `parse_budget_policy` 时没有 `unit/units` 键，解析结果 `limits={}`：**token/QC/步骤预算被静默丢弃，零强制且无告警**。

**生产结算只写两个维度**
- `src/acp_services/runtime/local_admission.py:671–680`：
  ```python
  counters=UsageUnitCounts(task_ref=1, attempt=int(...)),
  ```
  没有 recipe_step/qc_invocation/bsub/model_request。
- `research_budget.py:265–268`（`model_tokens` note）与 `:600–607`（`not_verified`）承认 model-token 未验证。
- 模型代理自述："acceptance harness, not a shipped RPH component… product makes no claim of equivalent pre-request budget enforcement"（`docs/reference/next-change/acp2-contracts/experience/model-budget.md:9–13`）。

**Supervisor 的 used 只是内存**
- `rph/packages/dsh-bundle/skills/supervisor.ts:372`：`const used: MutableBudget = zeroBudget();`，每次 `runSupervisor` 从零开始；`prior_evidence` 只用于 `lastReport`（`:380`），不恢复 used。`:579–586` 每轮按整份 plan budget 累加（估算，不是回执）。`:579` 还有一处 `used.model_requests = used.model_requests;` 的空操作，说明该字段的计费路径未完成。

**approval 与 proposal 的预算投影不是一套**
- approval 绑定 `budget_digest(budget)` 是任意 mapping 的 canonical digest（`src/acp_services/runtime/approvals.py:184–189`，比较在 `:876–880`）。
- proposal/plan digest 的预算字段是 C 的 8 字段投影（`src/acp_services/runtime/canonical_digest.py:486–495`，`C_PLAN_BUDGET_FIELDS`，由 `938e894` 2026-10-23 16:12 引入）。
- 换言之：RPH 审批卡绑定 8 字段预算，平台 submit 只认 `{unit,count,timeout_s}`，两者形状不同；没有代码把 8 字段转换为 ledger policy。

### 根因链

**技术根因**
1. 三种（加代理共四种）预算词表各自定义、各自解析：
   - 应用 submit budget（`task_count/core_hours/walltime_s`）由 B03 应用契约在 `f67f90f`（2026-10-22 22:51）引入，早于 B17；
   - ledger 词表（`task_ref/.../model_request`）由 `7f0fa7e`（2026-10-23 13:59，B17）引入；
   - RPH `PlanBudget`（token 级 8 字段）由 `f413bf8`（2026-10-23 07:15，C 轨）引入；
   - 代理 ledger 由 `51a7164`（2026-10-23 05:43）引入。
   没有一次提交负责"统一/换算"，只有 `usage_units.py` 的局部 alias 与 `parse_budget_policy` 的兼容分支。
2. 生产结算只实现 task_ref/attempt（`local_admission.py:671–680`），其他单位只存在于表/端点/测试；`v2_budgets` 的 settle 需要外部 POST，没有 controller 内部 producer。
3. 形状不兼容没有被 fail-fast：RPH 8 字段被静默解析为 `limits={}`；`core_hours/walltime_s` 反而抛未被捕获的异常。两种失败模式都不符合"未实现维度必须显式 recorded-only/NOT_VERIFIED"。
4. approval 只做不透明 digest 绑定，不做语义转换，因此"审批上限如何映射到实际预留"在平台侧没有实现点。

**设计/流程根因**
1. 预算被三个轨分别当作"计划字段（C）""提交绑定字段（B03）""执行计量（B17）"和"模型网关（C09）"处理；计划没有指定唯一预算身份与换算函数，也没有跨轨验收（例如用同一个 `PlanBudget` 完成 approve→submit→reserve→settle）。
2. B17 的"enforced/recorded-only/NOT_VERIFIED"声明是**能力自述**，不是对生产 producer 的断言；验收只验证 ledger API，不验证 controller 是否真的产生这些 receipt。
3. C 的验收脚本仍按"B16/B17 未交付"写 NOT_VERIFIED（`approval-budget-live` 09:41 提交早于 B17 13:59），后续没有按新交付重跑，导致预算证据与实现状态脱节。

---

## G14：研究数据有存储与只读接口，没有可用生产写入链

### 验证结论：**确认**

### 报告原声明不准确 / 需补充处

1. 报告说"`v2_research.py:1–16` 明确说明 P2 writer 尚未交付"——准确；原文在 `:11–15`（"until those P2 writers are delivered the store has no production producer…"）。
2. 报告把 G14 的阻断主要归到 G02（RPH 经 P2 工具）是对的，但**G14 还有独立于 G02 的一层缺口**：即使不看 MCP，平台应用层也没有 research resource 的 create/CAS 用例或 HTTP 写入端点，`ResearchResourceStore.create/compare_and_swap` 只有 acceptance 和测试直接调用。建议把这两层分开写，否则会让人误以为"补上 P2 工具即可闭环"。
3. 报告说"没有 writer"准确；需补充证据：B18 自己的验收 `research-resource-cas.json` 是通过 acceptance harness **直接 new store + create** 得到的（`scripts/acceptance/acp2_runtime.py:9340–9346`），`manual_browser_fixtures` 也是先 `store.create(...)` 再让 HTTP 读（`scripts/acceptance/acp2_experience_scenario_manual_browser_fixtures.py:459–462, 511`）。这正是报告警告的"seed fixture 读取成功冒充闭环"。
4. `server.py:356` 行号正确；store 走独立 DB `acp_research.db`，与 jobs DB / approval DB 分离，这一点报告未提，但影响"审批记录与研究记录是否同一事务边界"的判断。

### 证据

**存储层存在**
- `src/acp_services/data/research_resources.py:350` `class ResearchResourceStore`；`:446` `create()`；`:498` `read()`；`:531` `compare_and_swap()`；表定义 `:375`。
- `src/acp/api/server.py:356`：`app.state.research_resources = ResearchResourceStore(run_root_path / "acp_research.db")`（独立 DB）。

**生产 API 只有读**
- `src/acp/api/v2_research.py:1–16`：模块自述 "read-only UI projection… never writes… until those P2 writers are delivered the store has no production producer"。
- 路由只有两个 GET：`:200` `GET /research/proposals/{proposal_id}`、`:215` `GET /research/records/{research_id}`。
- 未种子时返回 404（`ResearchResourceNotFoundError`，store `:507–508`），符合边界但不构成写入能力。

**谁调用 create/CAS**

`grep -rn "ResearchResourceStore(\|ResearchResourceRequest(" src/ scripts/ tests/ --include=*.py` 的分布：

- `src/`：只有 `server.py:356` 实例化，`v2_research.py` 只读；无 `create`/`compare_and_swap` 调用。
- `scripts/acceptance/acp2_runtime.py:9340–9346`：`resource_store.create(ResearchResourceRequest(...))`。
- `scripts/acceptance/acp2_experience_scenario_manual_browser_fixtures.py:459–462`：seed store；`:511` 再启动/读取。
- `tests/test_acp2_runtime_science_binding.py:662/715/754`、`tests/test_acp2_research_resource_key.py:96`：测试直接用 store。

**P2 写入工具缺失**
- `src/acp_services/entrypoints/mcp.py:211–240`：`McpTool.ALL` 只有 8 个 P1 工具。
- `src/acp_services/entrypoints/mcp.py:1061–1066`：`initialize` 无条件返回 `acp.research.v1`、`version: 1.1.0`、`negotiated: true`，但 `_tools_list`（`:1073–1074`）仍只列 8 个；extension 宣告与实现不一致（G02 已详述）。
- `docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json`：`status: "delivered"`、`binding_ownership.B18 = ["P2-R1","P2-R5"]`，且规则写明 "the binding owner ships the live tool, the ControllerClient method and the application use case together; a Data interface delivered without a live tool is not closure of R3-02"。实际 `controller_client.py` 无 research 方法（`grep -n "def .*research\|proposal\|approval" src/acp_services/entrypoints/controller_client.py` 无命中），mcp 无 P2 工具。
- RPH 侧已按 18 工具写映射：`rph/packages/bridge/src/index.ts:507–585`（10 个 `acp_research_*_v1`），运行时必然 `unknown_tool`。

**审批写入没有进入 research store**
- B16 的 `ApprovalService` 使用 `manager.store.db_path`（`v1_routes.py:8310–8318`），即 jobs DB；research store 是 `acp_research.db`（`server.py:356`）。审批记录不是 research resource 的生产者。

### 根因链

**技术根因**
1. Data 层（B18）交付了版本化 store + CAS + 测试，但没有应用命令/HTTP 写端点；`v2_research` 只读。
2. MCP P2 工具（proposal_create/read/update_cas、approval_*、research wait/snapshot/resume）在 `mcp.py` 中完全不存在；extension 却宣告 negotiated=true。RPH bridge 的 10 个 P2 工具名没有服务端实现。
3. ControllerClient 没有 research 方法，P2 表的"live tool + client method + use case"三者一个都没绑定；因此即使 store 可用，也没有生产消费者。
4. 验收用 harness 直接 new store + create 来制造数据，再验证 HTTP 读；这只是读路径证据。

**设计/流程根因**
1. **单一提交的范围决定**：`ec4a0b3`（2026-10-25，"final acceptance remediation — read-only science/research routes"）在同一提交新增 `v2_research.py`（只读）、`science_projection.py`、`v2_science.py` 等 47 个文件；提交信息与模块 docstring 都明确"read-only projections"。这是为关闭验收边界而做的**有意切片**，writer 被推迟到 "P2 writers"。
2. **P2 writer 没有 owner**：计划把 P2-R1/P2-R5 绑定给 B18，但 B18 的交付证据只覆盖 Data CAS 直调；mcp/controller_client 的 live 绑定缺失。C18 计划又写明"B Data 持久接口未交付时仅 fixture 开发，不自行搭 SQLite/任务表"（`docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md:337` 附近），于是 C 也没有写路径。
3. **验收口径**：`manual_browser_fixtures` 以 seed 后 HTTP 读通过来证明面板；没有任何验收要求"未经 seed 的项目从生产入口创建 proposal/record"。因此 G14 不会被现有 harness 判失败。

---

## G16：科学工作台与插件体验仍是限定切片

### 验证结论：**确认**

### 报告原声明不准确 / 需补充处

1. **`capability-form` 不是"未找到"，而是计划中的明确交付物从未实现**：`docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md:419–422`（C27）要求新增 `workbench/features/capability-form.js`、支持冻结 JSON Schema 子集、并新增 `acp2_schema_form.test.js`。`reports/acp2_acceptance_defect_report_20261025/DEFECT_REPORT.md:344`（ACP2-DEF-015）已记录该文件与测试不存在。报告应引用该计划与既有缺陷，而不是只写"当前 frontend 未找到"。
2. **descriptor 本身不是缺口**：内置 capability descriptor 已有 `input_schema/output_schema`（`src/cccp/calculation/capabilities/builtin.py:173–174` 等），`GET /api/v2/science/capabilities` 也会把 `descriptors` 原样返回（`src/acp/api/science_projection.py:442–450`）。缺口是**前端未消费 descriptor 的 schema**：`grep -rn "input_schema\|inputSchema\|descriptors" frontend/js` 在非 vendor 代码中无命中；`science-question.js:393` 只取 `capabilities.questions`。
3. **`science_projection.py` 是验收补救切片，不是完整的 schema-driven registry**：整个文件由 `ec4a0b3` 新建（1204 行），`_QUESTION_WORKFLOWS` 固定三元组（`:75`），`question_specs`（`:335`）只输出 3 个问题；报告的行号正确，但应说明这是"为只读验收新增的投影"，而非"产品动态表单被裁成三种"。
4. 报告说"前端 science-question 已有默认来源、输入处理与预检防陈旧逻辑"——准确；具体在 `science-question.js:312–360`（`defaultsFor`/`sourceLineFor`/固定 `fieldSpecs`）、`:217–228, 434–446`（`inputRevision`/AbortController 防陈旧）。但这也说明它是**固定 6 字段的最小表单**，不是 schema-driven。

### 证据

**后端只暴露 3 个问题**
- `src/acp/api/science_projection.py:75`：`_QUESTION_WORKFLOWS = ("singlepoint", "optimize", "frequency")`。
- `:335–420` `question_specs()`：只为这三个 workflow 生成 `question_id/label/workflow/method_schema_id/level_id/engine/basis/defaults/limitations`；defaults 来自 `METHOD_SCHEMAS`/`FIELD_DEFINITIONS`（`:326–400`），不是来自 descriptor 的 `input_schema`。
- `:427–450` `science_capabilities()` 同时返回 `questions`、`descriptors`、`domain`，说明 descriptor 可用但问题表单不用它。

**前端固定字段**
- `frontend/js/workbench/features/science-question.js:333–370` `fieldSpecs()`：硬编码 `method/charge/multiplicity/solvent/temperature/standardState` 六个字段及 `visible/required`，没有按 schema 生成。
- `:393`：`state.questions = ((body && body.capabilities && body.capabilities.questions) || []).slice();` 只消费 questions。
- `:32–95`：内置 fixture capability catalog 也是 3 个 question。
- `grep -rn "capability-form\|capability_form" frontend/ src/ | wc -l` → `0`。

**插件/动态表单在代码中不存在**
- `src/cccp/plugin/discovery.py:1–20`：插件发现是"metadata-only / inert"，只读 manifest，不安装、不 import、不调用 loader；`PluginHost.enable` 后才 lazy load，且"Plugins can add a read-only analysis capability, never a second task chain."
- 全仓没有 `schema-form` 场景实现/证据目录（计划 C27 要求 `c_accept schema-form-live`）。
- 既有缺陷报告：`reports/acp2_acceptance_defect_report_20261025/DEFECT_REPORT.md:108`（ACP2-DEF-015）、`:344`。

### 根因链

**技术根因**
1. `question_specs` 的输入是 `WORKFLOW_CATALOG` + `METHOD_SCHEMAS`，`_QUESTION_WORKFLOWS` 是模块常量；新增 capability 不会自动变成问题（除非改代码并加 catalog 条目）。
2. 前端 `fieldSpecs()` 是固定白名单；capability descriptor 的 `input_schema` 没有被任何前端代码读取，也没有 JSON-Schema 子集渲染器。
3. 计划要求的 `capability-form.js`、`acp2_schema_form.test.js` 不存在；`schema-form-live` 验收未执行，因此没有门发现。

**设计/流程根因**
1. **分期设计**：计划 C 把 P2 最小表单（T-17/C11，`science-question.js`）与 P3 动态表单（C27，`capability-form.js`）明确分开；P2 交付了固定表单，P3 未交付。报告把两者合在一起说"限定切片"容易让人误以为 P2 表单也不存在；应区分"P2 最小表单已交付、P3 schema 表单未交付"。
2. **验收补救切片**：`ec4a0b3` 为关闭"科学只读路由"验收新增了 `science_projection.py`，用 3 个问题/只读 ViewModel 证明投影链；它不是 C27，也没有被当作 C27 的实现。随后没有 C27 的实现提交。
3. **H5 归因问题**：既有缺陷报告指出 H5 的 PASS 来自 B/A 插件证据，C26–C28 均为 `[~]`，C 侧不可签署（`DEFECT_REPORT.md:344–350`）。因此"插件体验未交付"不是本轮遗漏，而是 P3 从未进入实现/验收。

---

## 对报告的修正建议

1. **G11**
   - 行号改为 `research-approval.js:524–526`（fixture 注释）、`:584`（`apr-*`）、`:698–705`（`submitted/dispatch_ref`）。
   - 增加"默认页面为空态"的说明：`ACPResearchApproval` 只在测试与自身文件中被引用，没有生产调用者注入 proposal/card；`persistence` 适配器也从未注入，卡片不跨刷新。
   - 把"B16 not delivered"描述为**时序性 stale 注释**（模块 07:31 → B16 13:05），并引用验收脚本 `FORBIDDEN_PATTERNS`/`fixture_content_no_platform_transport` 说明为何没有门发现未接线。
   - 明确 C 的写通道选择：B16 是 HTTP，C 研究客户端是 MCP P2（G02 阻断）；不要只说"服务端已有端点"。

2. **G12**
   - 把"省略 plan/proposal/budget"改为"省略 `plan_digest`/`proposal_ref`/`proposal_digest`/`budget` 四个字段"。
   - 引用 MCP schema 的"never enforced here — the Controller remains the gate"，把问题定位为**入口策略缺失**，而不只是字段集合不一致。
   - 补充 `req.budget` 只在 `v1_routes.py:8387` 出现一次、budget-only 时被完全丢弃；补充 budget-only 仍触发严格 project scope 的非对称。
   - 根因段落点名 `f67f90f`（含 budget 的 launch 判定）与 `e5463de`（不含 budget 的 approval gate）两个提交，以及 `PrincipalKind` 无 agent 类。

3. **G13**
   - 把"无统一换算契约"改为"只有 `task_count→task_ref` 的局部 alias，缺 `core_hours/walltime_s` 与 RPH 8 字段的转换"。
   - 加入探针结果：`core_hours/walltime_s` 抛 `UsageUnitValidationError`；RPH `PlanBudget` 解析为 `limits={}`（静默零强制）。
   - 修正"recorded-only"表述：production 只 settle `task_ref/attempt`；其余维度没有自动 producer，只有手动 `POST /research/budgets/{id}/settle`（消费者仅 acceptance）。
   - 指出 `UNIT_CAPABILITIES[MODEL_REQUEST]=RECORDED_ONLY` 与 `release_statement.not_verified` 的内部矛盾。
   - 增加 `v2_budgets.py` 已交付，C 验收脚本 `approval-budget-live:130` 的"未交付"文本已 stale。

4. **G14**
   - 把缺口拆成两层：①平台应用层无 create/CAS 用例/端点（独立于 G02）；②MCP P2 工具缺失（G02）。
   - 引用 B18 证据的生成方式（acceptance 直接 `new ResearchResourceStore` + `create`）与 manual-browser seed，说明"读通过"来自 seed。
   - 注明审批存储在 jobs DB，research store 在 `acp_research.db`，两者不是同一写入链。
   - 引用 `p2-research-operations.v1.json` 的 `binding_ownership` 规则与 `status: delivered` 的矛盾。

5. **G16**
   - 引用计划 C27（`ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md:419–422`）与 `DEFECT_REPORT.md:344`（ACP2-DEF-015），把"未找到"升级为"计划交付物未实现且已有缺陷记录"。
   - 区分 P2 最小表单（`science-question.js`，固定 6 字段，已交付）与 P3 schema 表单（`capability-form.js`，未交付）。
   - 说明 descriptor 已有 `input_schema`（`builtin.py:173`）且 API 已返回，缺口在前端消费与 preflight/submit 绑定，不是 descriptor 缺失。
   - 注明 `science_projection.py` 由 `ec4a0b3` 作为只读验收补救新建，`question_specs` 的 3 项是文件内常量，不是产品全量 capability 列表。

---

## 附：本次使用的关键命令

```bash
git log --format='%h %ad %s' --date=iso --follow -- frontend/js/workbench/features/research-approval.js
git log --format='%h %ad %s' --date=iso --follow -- src/acp/api/v2_approvals.py
git show e03a9dd --stat --format='%H %ad%n%B' --date=iso
git show e5463de --stat --format='%H %ad%n%B' --date=iso
/usr/bin/python3.11 /tmp/opencode/lane_c_g12_probe.py
/usr/bin/python3.11 /tmp/opencode/lane_c_g13_probe.py
grep -rn "ResearchResourceStore(\|ResearchResourceRequest(" src/ scripts/ tests/ --include=*.py
grep -rn "capability-form\|capability_form" frontend/ src/ | wc -l   # 0
```

**限制声明**：G12 未提交真实任务，G13 的 `core_hours` 异常未验证到 HTTP 状态码（只到 helper 层），G14/G16 未启动服务或浏览器；上述"影响"均为代码路径推导，不冒充端到端运行证据。
