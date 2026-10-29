# ACP / RPH 当前实现、验证范围与剩余缺口

核查日期：2026-10-28。源码基线：`b32f22bb`。本说明记录该基线的代码与仓库内证据，后续修改须重新核对受影响范围。

**2026-10-29 补充**：后续[完整功能审查](../../../reports/acp2_function_audit_20261028/REPORT.md)在相同源码树的 `fd2087c7` 上完成广泛回归。非慢回归 12125 PASS / 1 FAIL / 26 SKIP，真实 SDK 7 PASS；浏览器首次 26 PASS / 18 FAIL，其中 10 项经测试 Origin 隔离后复验通过。另确认 Workbench 操作遮挡、数值编辑问题和当前 BOM 制品路径缺失。下文第 5 节是前次定向核查记录，不替代这次结果；后续开发建议见[路线图第 6 节](<../../RPH_Agent_Three_Phase_Roadmap_20261027(1).md#6-审查后的开发建议2026-10-29>)。

本文是现状说明，不是新实施方案或发布签字。研发方向见[三阶段路线图](<../../RPH_Agent_Three_Phase_Roadmap_20261027(1).md>)；架构约束见[四组分方案](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)。历史聊天的读取过程未在本次核查中重新验证。

## 1. 当前结论

ACP 已有科学计算与研究运行时、MCP 研究扩展、RPH Supervisor 和多项真实验收。RPH 仍缺少默认产品入口的完整装配，以及同一条“真实模型规划→真实 QC→原生证据→后续研究决策”的验收。不能据此宣称完整自主科研 Agent 已交付。

按 RPH 三阶段目标判断：第一阶段已有多数基础能力和分项证据，尚未整体通过；第二阶段已有运行时和部分真实科学链，仍缺闭环；第三阶段的多 Agent 调度、共享假设图和协作收益尚无交付证据。这里的阶段判断不替代既有 G/H 门。

## 2. 代码、真实证据与边界

| 能力 | 当前实现或证据 | 尚不能据此宣称 |
|---|---|---|
| 科学执行与客户端边界 | `cccp/qc → cccp/backends → cccp/calculation → acp`；`acp_services` 持有平台服务；RPH 与 Workbench 共用 Controller | RPH 可以绕过 ACP 直接调用 QC/SSH，或建立第二任务权威 |
| MCP | stdio 入口；八个基础工具；协商后开放十个 `acp.research.v1` 研究工具 | MCP Streamable HTTP 已实现，或所有 harness 均已兼容 |
| 研究运行时 | ResearchSession、ResearchRecord、审批、预算、提交意图、Saga、事件游标、证据投影与决策校验 | 每个真实科研场景都已经覆盖恢复与证据路径 |
| 模型 | M4-02 记录真实 `deepseek-chat` 调用三次，含首次规划与证据反馈后的规划 | 模型已自主驱动同一条真实 QC 科研链，或生产凭据授权已关闭 |
| 本地科学 | M4-02 的 `singlepoint`、`optimize`、`frequency`、`xtb_optimize` 真实完成 | 这四项均使用相同的冻结、回执和恢复路径 |
| LSF | M4-03 的 15 项真实提交、控制、deadline、恢复、发布、预算与身份场景通过 | 限定 DA 的 LSF 科学链也已通过；该次远端经 localhost SSH/Openlava 实测，不代表任意集群部署 |
| Workbench | M4-02 有 Chromium/Edge 同任务展示、真实审批/预算浏览器记录 | 全部科学产品和第三方分析结果都有专用展示面板 |
| 发行 | 四个 Python 发行与 RPH 离线制品；M5-03 第二轮独立操作者安装复验 PASS | 完整 ACP2 放行、生产部署或生产服务已更新 |

### 2.1 MCP 实际工具面

八个基础工具为 `acp_capabilities`、`acp_preflight`、`acp_submit`、`acp_status`、`acp_cancel`、`acp_prepare_artifact`、`acp_read_artifact`、`acp_analyze`。

十个研究工具覆盖 proposal create/read/update CAS、approval request/query/decide/revoke、wait、snapshot、resume。客户端须先经 `acp_capabilities` 完成应用层协商，再读取协商后的工具列表。未协商会话只列八个基础工具，独立安装回执中的“tools/list 恰好 8 个”与代码中的“8+10”并不矛盾。

依据：[MCP 注册与协商](../../../src/acp_services/entrypoints/mcp.py)、[Controller 装配](../../../src/acp/services_mcp_bootstrap.py)、[RPH 会话](../../../rph/packages/dsh-bundle/src/platform-session.ts)。

### 2.2 真实模型和真实科学分别通过，尚未串成同一研究链

[M4-02 摘要](../../../reports/acp2_remediation_20261026/m4-02/summary.json)的模型分支调用真实模型，但科学派发仍采用声明过的 `fake` 工作流；真实 ORCA/xTB 计算在独立 science 分支执行。模型反馈轮所读的是该受控分支的证据。浏览器、身份与注入拒绝也各有声明范围。

因此，本轮已有“模型接线可用”和“科学程序可用”的分项证据，仍缺同一条真实模型＋真实 QC＋原生证据＋后续决策轨迹。不同分支的 PASS 不能拼成这一整链的 PASS。

### 2.3 RPH 产品入口与科研角色

发布的 CLI `main.ts` 调用 `main()` 时没有注入产品组件；非 demo 模式下 `defaultParts()` 依赖 `dependencies.parts`，没有注入则返回空对象，再由 readiness 检查拒绝运行。接口和拒绝行为已经实现，默认产品组件并未自动装配。仅安装制品、配置环境变量不能据此推定 `rph research` 已可开展真实研究。

需要装配的组件包括模型 Planner/gateway、持久 research writer、证据和预算读取、提交参数映射等。正式证据读取仍依赖注入 `EvidenceReaderPort`；平台已有研究记录证据 HTTP 路由，但 RPH 禁止直接 HTTP，不能把存在该路由写成 MCP 消费链已经闭合。

生产 skill 组合要求模型 Planner；其余 interpreter、synthesizer、reviewer 默认是确定性 helper。默认 synthesizer 以问题和轮次构造候选文本，reviewer 按缺失摘要和证据状态给建议。这提供边界检查，尚不构成开放式机理假设生成、专家审查或充分的策略修订。

依据：[CLI 组合根](../../../rph/packages/cli/src/compose.ts)、[发布入口](../../../rph/packages/cli/src/main.ts)、[skill 组合](../../../rph/packages/dsh-bundle/skills/composition.ts)、[端口适配](../../../rph/packages/dsh-bundle/src/research-ports.ts)。`rph/` 当前没有便携 `SKILL.md` 包，第二个 harness 的迁移验收尚无记录。

### 2.4 执行链和多步骤配方

科学能力迁入 CCCP 与普通提交启用 envelope 是两个判断。当前 `DEFAULT_ENVELOPE_WORKFLOWS = {singlepoint} ∪ RECIPE_WORKFLOWS`；普通 `optimize`、`frequency`、`xtb_optimize` 等尚未统一推广到该默认入口。[执行范围](../../../src/acp_services/runtime/execution_chain.py)与 M4-02 的 legacy 回执缺口应一起阅读。

bounded DAG 已接入执行链，步骤类型包括 singlepoint、optimize、frequency、irc；其存在不等于任意科学 Recipe 都能编译。限定 DA 的 endpoint_prep、candidate_path、energy_thermochemistry 尚未完整通过既定公开配方入口，回执记录 `unsupported_step_kind`。真实九步流程通过多个 attempt 执行，未达到该项验收要求的一个 bounded TaskRef 管理完整配方。

“一项研究含多个 TaskRef”本身不是错误。后续需要讨论计算 DAG 与研究层操作如何分工、如何保存跨任务依赖和恢复；本次说明不替后续设计作选择。

依据：[DAG](../../../src/acp_services/runtime/step_dag.py)、[配方转换](../../../src/acp_services/runtime/step_definition.py)、[DA 回执](../../../reports/acp2_remediation_20261026/m4-04-route-a/REPORT.md)。

### 2.5 DA 的执行通过与科学命题

route-a 和 16 核复跑完成了真实 TS、频率、双向 IRC 与分析。回执的 `scenario_result=PASS`，但 DA-C05 端点关联 `claim_status=refuted`，路径连通性问题为 refuted；平台与 RPH 均要求 revise。能量子问题的 supported 不可扩展成整个机理 supported。

执行采用显式记录的 `r2SCAN-3c/def2-mTZVPP` 变体，不能写成原定 `wB97X-D4/def2-TZVP` 已通过。已知候选路径的验证不能写成自主发现新路径。Local DA 行缺少规定的 walltime/terminal-usage 回执、计时基准未闭合，DA-ENV-LSF 仍为 NOT_VERIFIED。

依据：[route-a](../../../reports/acp2_remediation_20261026/m4-04-route-a/REPORT.md)、[16 核复跑](../../../reports/acp2_remediation_20261026/m4-04-route-a-16core/REPORT.md)。

## 3. 仍需开发、验证与记录同步的事项

| 类型 | 事项 | 完成边界 |
|---|---|---|
| 产品装配 | 默认 RPH 入口、模型/持久 writer/证据/预算/提交映射；便携 skill | 由正式入口运行，缺依赖有明确错误，具备同一条真实交互轨迹 |
| 执行与配方 | 普通工作流的 attempt 链推广；完整科研配方或跨 TaskRef 研究依赖 | 声明范围内的身份、回执、发布、额度与恢复一致；不能只补文档关闭 |
| 科研行为 | 理解、假设、审查、证据反馈后的有限策略修订 | 固定小型基准及失败案例；模型叙述不作为科学证据 |
| 真实验证 | 同一条模型＋QC 闭环；DA 缺少的 Local 回执、计时基准及 LSF 行 | 分别保留 PASS/FAIL/NOT_VERIFIED，不能互相替代 |
| 运行准备 | M3-03 的生产模型凭据轮换、授权和限额 | 已有开发期模型成功记录不能替代生产准备验收 |
| 迁移与协作 | 第二 harness、多 Agent 角色/提案/共享假设图/合并与去重 | 尚无交付证据；后续讨论具体实现 |
| 文档与候选对账 | 下表列出的过期 notes 与正式门关联 | 后续由候选 owner 带证据重新对账；不在本次文档修订中生成发布签字 |

## 4. 正式台账与过期叙述的对应关系

[机器台账](../next-change/acp2-contracts/release/evidence-coverage.current.json)共有 39 tasks、32 checkpoints、26 gaps、8 H rows、3 DEFERRED rows。仍有 5 个 gap OPEN：G05/G10/G13/G15/G18；任务 M3-03 OPEN；H3/H4/H7 OPEN；`candidate_full.allowed=false`。任务完成数不能换算成“完整科研产品已完成百分之多少”。

| 记录 | 已确认事实 | 应如何阅读尚未同步的内容 |
|---|---|---|
| M5-03 与 H7/G18 | [第二轮独立操作者结果](../../../reports/acp2_remediation_20261026/m5-03/m503-operator-receipt-round2/RESULTS.md)已 PASS；当前任务 M5-03 也为 CLOSED/PASS | scope、H7/G18 部分 notes 仍称独立操作者 absent/尚未执行，这是过期叙述；H7/G18 正式状态仍保持 OPEN/NOT_VERIFIED，不能据一项安装回执自动关闭聚合门 |
| execution_limits 与 H3 | 当前代码的 walltime_process_termination、queue_deadline 已为 enforced；已有 M4-03 原生回执 | M4-03 运行当时的 summary 和部分 H3/M5-02 文字仍记录升级前状态；它们是历史快照。通用 deadline 通过不补齐 DA 自身缺少的回执和 LSF 行 |
| G15 与 summary.notes | endpoint_prep 等配方入口仍有代码支持缺口 | “剩余都是环境/凭据问题、非实现问题”的概括不准确，不能覆盖该实质缺口 |
| M4-02/model README | 模型为真实 gateway，QC 为受控 fake 派发 | 原生成模板把模型也写成 fixture/未执行，须按模型摘要和分支边界阅读 |
| RPH doctor/connect | 已有 MCP/DSH 实测和独立安装记录 | 尚不能认定发布 CLI 的 `rph doctor --connect` 已在正式装配上通过；其专属行仍需记录 |

这些说明修正事实解释。冻结的 JSON、原始回执、摘要、BOM/manifest 与其摘要链维持原记录；状态变更需要适用证据和候选级重新对账。生产部署、完整科学门和 DEFERRED 能力不因本次文档修改获得 PASS。

DEFERRED 仍包括 worker-hosted 第三方 COMPUTE、CDXML、累计 core-hours/token 硬强制。纯分析插件验收不关闭 worker COMPUTE；记录 token/core-hours 不等于强制其累计上限。

## 5. 本次核查的验证范围

2026-10-28 在上述源码基线上补跑：

- 定向 Python 回归：220 passed、5 skipped、2 deselected。覆盖 MCP 研究协商、ResearchSession、EvidenceView、Saga、bounded DAG、M4-02/03/04 结构与逻辑、RPH 安装面和 coverage sign-off；跳过项不算真实场景 PASS。
- `pnpm --dir rph test`：20 个测试文件通过，包含构建；`pnpm --dir rph check` 的边界与类型检查通过。
- `python3.11 scripts/acceptance/validate_acp2_gap_coverage.py`：VALID；仍报告 5 gaps open、full release NOT_VERIFIED。
- `python3.11 scripts/acceptance/check_execution_limits_consistency.py --check`：PASS，0 findings、6 units、6 execution limits。

本次没有重新调用真实模型、QC、LSF 或浏览器，没有更新生产服务；这些结论引用仓库内带范围的历史回执。后续文档修订本身不构成重新验收。
