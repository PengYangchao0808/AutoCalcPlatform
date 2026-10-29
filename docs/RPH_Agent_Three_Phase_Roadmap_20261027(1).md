# RPH Agent 三阶段开发大纲

初稿：2026-10-27；现状修订：2026-10-28；功能审查后的开发建议：2026-10-29。
定位：ACP 四组分方案的 RPH 专项附件；说明目标、阶段边界与建议实施顺序。第 6 节是基于审查的开发建议，尚未开始功能实现。
核查仓库：`/ACPServer/AutoCalcPlatform`，最新审查基线 `main/fd2087c7`（与 `b32f22bb` 的 Git 树相同）。初稿的 Windows 路径及 `484764f6` 仅为当时观察，不能作为当前候选或验收 revision。

当前实现、证据来源、未完成事项及台账过期叙述统一见[ACP / RPH 现状说明](reference/current/ACP_RPH_Status_20261028.md)。本文不构成发布签字，也不声明下述三阶段已完成。

最新[功能审查](../reports/acp2_function_audit_20261028/REPORT.md)发现 Workbench 操作遮挡、数值编辑缺陷、测试隔离/构建断言问题及发行物取回缺口。因此三阶段前增加基线修复，不再把“已有分项 PASS”视为可直接扩展 Agent 的充分前提。

## 1. 要解决的问题与当前起点

目标是让用户通过自然语言和结构输入提出科研问题，由 Agent 发现平台能力、形成计划、受控执行、读取科学证据，并据此继续、修订或停止研究。研究进度、任务与结论需要在断线、重启、会话或 harness 切换后可追溯，科学结论与任务完成分开判断。

需求沿用初稿提炼：skills＋MCP 调用 ACP；保留 CCCP 和集群能力；兼容不同 harness；RPH 与 Workbench 并列接入平台。初稿关于历史聊天的读取过程，本次没有重新验证；聊天叙述本身不作为 ResearchRecord 中的科学证据。

现有四组分是 CCCP、ACP CLI/Services、Workbench、RPH；科学执行四层是 `cccp/qc → cccp/backends → cccp/calculation → acp`。两种划分并存，新科学计算能力仍以 CCCP 任务层为唯一执行根。

当前已经有 RPH 四包、Supervisor、四种 skill 接口，以及研究记录、审批、预算、事件、恢复和证据投影。MCP 不止历史 B26a 握手：后续已有真实 DSH/模型、本地 QC、LSF、浏览器和独立安装的分项回执。不过，M4-02 的真实模型分支使用 fake 科学派发，真实 QC 在独立分支执行，尚不能认定同一条模型＋QC 自主研究链已通过。

## 2. 三阶段目标与当前距离

| 阶段 | 目标与代表场景 | 当前已有基础 | 尚需满足的阶段条件 |
|---|---|---|---|
| 第一阶段：skills＋MCP 交互闭环 | 固定输入、方法和预算，完成自然语言请求→发现→预检→一次真实 xtb_optimize→查询→prepare/read→带任务/工件引用的结果 | MCP 8+10 工具、真实模型接线、本地 QC、同任务多入口展示、幂等/恢复/取消的分项证据 | 默认产品装配和便携 skill；同一条真实模型＋QC 轨迹；在该链上核对重连不重复提交及取消；第一阶段尚未整体通过 |
| 第二阶段：单 Agent 科研闭环 | 一个 Supervisor 调用不同 skill，推进构象筛选→优化→频率→单点/热化学，并按适用范围扩展 TS/IRC | 持久研究运行时、可恢复 Supervisor、模型 Planner、bounded DAG、真实 DA 部分科学链 | 科研理解/假设/审查/策略修订、完整配方或跨任务依赖、原生证据消费、受控失败恢复、预算/轮次停止；Local 与 LSF 分别留证 |
| 第三阶段：多 Agent 机理研究 | 主管接收专业 Agent 的结构化提案，管理竞争机理、路径、验证、可比自由能与报告 | 可复用既有 Proposal、ResearchRecord、证据引用、预算和执行平台 | 角色调度、共享假设图、提案冲突处理、去重/恢复及相对单 Agent 的收益验证；尚无该阶段交付证据 |

第二阶段的四种 skill 是同一 Agent/主管的不同职责，不表示四个专业 Agent 已就绪。当前生产组合要求模型 Planner，另外三类默认 helper 为确定性实现。阶段验收要检查实际科研行为，不能只检查函数名和 Schema。

DSH 是参考接入，第二个 harness 用于验证迁移；当前 `rph/` 尚无便携 `SKILL.md` 包或第二 harness 迁移回执。TS/IRC 按能力注册与适用范围启用；已知候选的验证不能写成自主发现路径。结论证据不足时保留 inconclusive/not_evaluated，证伪时保留 refuted。

## 3. 代码职责与需要讨论的缺口

依赖方向沿用 `cli → dsh-bundle → bridge → research`。

| 位置 | 已有职责 | 尚需补齐或讨论 |
|---|---|---|
| `rph/packages/research` | ResearchRecord、Proposal、证据引用、记忆与提交意图契约 | 假设图与跨任务研究状态的表达；不持有任务数据库 |
| `rph/packages/bridge` | 科研操作、TaskRef、错误、扩展协商；注入 PlatformClient | 补齐所需公开消费路径，保持无 HTTP/fetch/进程执行 |
| `rph/packages/dsh-bundle/skills` | Supervisor、四种 skill 契约、模型 Planner 与恢复逻辑 | 便携提示/Schema、科研理解、假设、审查和受证据约束的策略调整 |
| `rph/packages/dsh-bundle` | MCP 会话、DSH 适配、profile 与端口组合 | 模型/持久 writer/证据/预算/提交映射的正式产品装配；其他 harness 薄适配 |
| `rph/packages/cli` | research/resume/doctor 入口与 readiness 拒绝 | 默认二进制未自动提供完整产品组件；不能宣称填环境变量后即可真实研究 |
| `src/acp/services_mcp_bootstrap.py` | MCP 与 Controller 装配 | 复用 Application API，按需补全研究消费入口 |
| `src/acp_services` | 身份、审批、预算、研究资源、Saga、工件、执行恢复 | 普通工作流 attempt 链推广、完整科研配方或跨 TaskRef 依赖与证据闭环 |
| `src/cccp/calculation/tasks` | 科学任务唯一执行根 | 真正缺失的科学计算能力在此补齐并注册 |
| `tests`、`rph/test`、`scripts/acceptance` | 契约、回归、故障和真实验收 | 同一条模型＋QC＋后续决策轨迹，以及跨 harness/多 Agent 验证 |

当前默认 envelope 范围是 `singlepoint` 加已注册 recipe；普通 optimize/frequency/xtb_optimize 尚未全部推广。bounded DAG 已存在，但限定 DA 的完整九步配方不能通过既定公开入口编译，多个 attempt 的真实执行不能替代该项单 TaskRef 验收。后续需要决定计算 DAG 与研究层跨任务编排的边界，本附件不预先指定实现。

## 4. ACP–RPH 通信与长期状态

```text
用户 → Harness/RPH → MCP stdio 适配器 → Controller HTTP Application API
                                     → Services/科学工作流 → CCCP/Worker → 本地或 LSF
Workbench ───────────────────────────→ 同一 Controller
```

入口为 `acp mcp serve --controller <url>`。适配器可随 harness 启停，任务持续由 Controller 管理，集群连接由 ACP 持有。现有 Controller HTTP/SSE 不表示 MCP Streamable HTTP 已实现。

基础工具继续复用：

- `acp_capabilities`：发现能力与协商扩展。
- `acp_preflight`：检查输入、参数和平台条件。
- `acp_submit` / `acp_status` / `acp_cancel`：提交、观察、取消。
- `acp_prepare_artifact` / `acp_read_artifact`：显式准备与读取工件。
- `acp_analyze`：调用已注册分析，不把任务完成自动解释为科学命题得到支持。

研究扩展已注册十个工具，覆盖 proposal 的创建/读取/CAS 更新、审批请求/查询/决定/撤销、wait、snapshot、resume。须先以 `acp_capabilities` 协商 `acp.research.v1`，再读取协商后的工具列表；未协商会话只看到八个基础工具。

通信和状态约束：

1. 提交返回持久 TaskRef；长任务用有界 wait/事件或限频状态查询，等待不持续消耗模型调用。
2. 重试复用原幂等键，计划/输入改变更新身份与键；绑定 job/attempt/release，工件绑定 generation/digest。
3. 任务状态归 ACP；研究轮次与决策通过平台资源持久保存，更新使用 revision/CAS。跨会话恢复不依赖聊天搬运。
4. 结构化错误与引用进入工具结果，大日志/轨迹作为工件按需读取。证据 HTTP 路由存在不等于 RPH 的 MCP 消费入口已闭合，当前仍需证据 reader 正式装配。
5. 多 Agent 的结构化提案、主管合并和统一预算是第三阶段目标；当前不据此宣称协作功能已实现。

单 Controller 权威状态继续沿用；Raft、多主及 Rust 重构不作为三阶段前置，未来替换内部实现时保持公开契约兼容。

## 5. 科学结论、验收与发布状态

研究循环为“问题→假设→规划→计算→证据→接受/修订/停止”。模型叙述不作为验证证据。

现有 DA route-a 的执行链为 PASS，但端点关联命题 refuted，平台/RPH 均要求 revise；采用了有明确偏差记录的计算级别，不能写成原定级别已通过。DA 的 Local 回执、计时基准与 LSF 科学行仍有缺口。M4-03 通用 LSF deadline 的通过不关闭这些 DA 专属要求。

M5-03 独立操作者第二轮安装复验已 PASS；正式台账仍有 G05/G10/G13/G15/G18 与 H3/H4/H7 等 OPEN，`candidate_full.allowed=false`。部分台账 notes 尚称“独立安装未执行”或“执行约束未 enforced”，与后续证据/代码不一致，按[现状说明第 4 节](reference/current/ACP_RPH_Status_20261028.md#4-正式台账与过期叙述的对应关系)区分。文档更正不自动变更冻结门或生产发布状态。

开发闭环继续采用“失败案例→定位归属→回归→修复→候选验收→可回退发行”。记录 ACP/RPH revision、契约/skill、harness/模型、科学软件与测试集版本；评价分别包括协议、流程、科学证据、恢复和成本。已有证据才能进入研究记忆，skill/recipe 升级经回归后晋升。

## 6. 审查后的开发建议（2026-10-29）

**保留三阶段方向，但把工作顺序改成“修复基线 → 正式入口真实闭环 → 有限自主科研 → 经比较验证的多 Agent”。** 以用户能完成的研究场景划分交付，每批都留下可回退版本和可复核回执。无需再建一套 Agent 框架、任务库或调度器。

### 6.1 前置批次：形成可信的开发基线

先解决审查 F01/F03 的科研面板遮挡与数值编辑，定位 F09 的查看器首次加载失败；F02 构建器参数断言与 F04 临时测试服务继承部署 Origin 分别修在测试层。10 项 Origin 相关测试已在隔离配置后通过，不能据原始失败去关闭产品跨域保护。修复后跑真实点击、输入与查看器回归，模块测试单独通过不足以放行。

核对 F08 的候选发行物保存位置：本机冻结 BOM 的 31 个 URI 均缺失，历史独立安装通过不等于当前可取回。应恢复/定位原制品并核对摘要；若重新构建，明确是新候选并重新验收。

完成条件是受影响回归通过、剩余 NOT_VERIFIED 有具体范围、后续第一阶段所用入口可安装和复现。已有 1064 条 lint 诊断与 189 个格式文件作为存量治理单独安排；不把一轮全仓格式化、全部 DEFERRED 实现或完整 DA 放行混进这一批。

### 6.2 第一阶段：先让一个正式产品入口可用

建议主入口选**安装后的 `rph research` / `rph resume`，DSH 作为第一个参考 harness**。Workbench 用于查看同一任务、证据和审批，复用平台能力；暂不同时建设另一套浏览器 Agent 主循环。

按以下次序完成：

1. **打通产品装配。** 在现有 CLI/bundle 组合根提供真实模型 Planner、持久 writer、EvidenceReader、预算读取与提交映射；测试注入接口保留。`doctor` 明确指出模型、权限、软件、存储及执行能力是否就绪。正常安装后的 bin 不需要开发者手动传 `dependencies.parts`。
2. **补齐公开研究消费面。** 逐项核对上述端口可否经现有 MCP/研究扩展访问，复用已有契约；确实缺失时在 Services/Application API 与 MCP 适配器补最小版本化操作，并更新扩展协商、工具表和文档。不要预先承诺 18 个工具永远不变，也不要让 bridge 私自 HTTP/SSH 或写数据库。
3. **先跑 `singlepoint` 工程闭环。** 它已默认走 envelope，适合先验证模型计划、审批、冻结、一次真实 QC、工件引用和恢复能否串通。这只是中间检查，不替代原定 `xtb_optimize` 场景。
4. **完成小分子 `xtb_optimize` 正式场景。** 固定输入、电荷/多重度、方法、资源与预算，补齐该普通入口所需的冻结/回执/发布/恢复路径，避免首条产品链继续依赖不完整的 legacy 回执。逐个工作流推广，不一次性切换全部 15 项。
5. **交付便携 skill。** 明确能力发现、预检、审批、提交、等待、证据读取和停止规则；提示/Schema/示例与 harness 装配代码分离，先在 DSH 上运行。第二 harness 的真实迁移验收放在第一阶段收尾至第二阶段早期，早于多 Agent，防止过晚发现接口依赖 DSH。

阶段验收必须来自**同一条真实轨迹**：自然语言输入 → 模型计划 → 平台预检/审批 → 真 xTB → 原生结果/ArtifactRef → 有引用的解释。先在本地通过，再按相同契约记录 LSF 场景，分别声明支持范围。模型可以选择拒绝或请求补充信息，不能为了通过测试硬发任务。

同链至少验证：断开 MCP 后重连不重复提交；RPH 重启后继续已有研究；取消/超时真正结束执行；证据缺失时给出 inconclusive；模型无效输出或越权计划被拒绝。记录输入、TaskRef/attempt/release、模型/skill/software 版本、证据摘要与实际使用量。调用数、轮次、任务数和墙钟限额要有明确执行位置；累计 token/core-hours 硬强制尚未交付，不能以统计值冒充硬上限。

### 6.3 第二阶段：有限、可审查的单 Agent 科研

首个场景建议收窄到**给定候选结构的优化 → 频率检查 → 按证据继续、修订或停止**。稳定后加入可比方法下的单点/热化学与候选排序，再接构象筛选；已知 TS/IRC 验证随后加入。首版不以“自主发现完整反应机理”为验收目标。

四种 skill 按职责实现，不要求四个独立模型或每步都调用模型：结构/参数/能力校验用确定性代码；模型承担问题解释、提出受限计划和解释证据；科学断言仍由验证器与原生产物判定。扩展默认确定性 helper 时，按真实失败案例增加能力，不以角色名字齐全判定完成。

研究状态至少持久化：问题/假设标识、待检验命题、计划 revision、依赖 TaskRef、证据引用、决策理由与停止原因。先扩展现有 ResearchRecord/Proposal；等多个候选和相互依赖确实需要时，再扩大假设图模型。

建议明确两层编排边界：

| 层次 | 负责什么 | 何时创建下一步 |
|---|---|---|
| ACP bounded DAG | 提交前已确定的计算依赖、固定参数与产物传递；冻结为可恢复的计算执行单元 | 满足预先定义的依赖/条件后执行，内部不运行开放式模型决策 |
| RPH 研究循环 | 读取证据，修订假设/方法，选择下一项任务或 DAG，决定停止 | 一个研究决策点形成新 Proposal/revision，并重新执行适用的预检/审批 |

跨 TaskRef 依赖保存生产者 attempt 与产物 generation/digest，重新计算使旧证据失效时显式处理；恢复先对账已有任务和证据，再决定是否新增提交。不能每恢复一次就重新规划并重复启动，也不能绕过预算/审批自动升级计算级别。

跨任务科研循环与完整单 TaskRef DA 配方是两个独立交付项：本路线允许先做前者，但 G15 的 endpoint_prep/candidate_path/energy_thermochemistry 缺口仍保持 OPEN，后续按原要求补齐和验收。

阶段评估使用小型、固定且包含负例的基准：正常成功、未收敛、虚频不符、证据不足、端点 refuted、预算停止、服务重启。检查决策是否正确、无效/重复任务数、恢复是否一致、耗时及成本；预先冻结判定规则。必须保留 DA 端点 refuted 这类反例，允许正确停止，不以“最后都输出 supported”为成功标准。

### 6.4 第三阶段：有明确收益时再增加多 Agent

开始条件：单 Agent 的正例/负例与恢复基准稳定，第二 harness 可迁移，真实瓶颈已能归因到任务分工。先试一种主管＋一种专业角色，例如主管请求独立证据审查；比较相同输入、模型与预算下相对单 Agent 的决策质量、重复计算、时间和费用。收益不足则保留单 Agent。

专业 Agent 提交结构化提案/审查意见，统一经主管合并和平台提交策略授权。新增多个角色不能产生多个任务权威或各自独立的无限预算。之后再按证据增加构象、TS、IRC、热化学角色、共享假设图和冲突处理。第二 harness 验收只验证可迁移性，本身不是多 Agent 协作验收。

### 6.5 建议拆分的开发批次

| 顺序 | 范围 / 主要代码驻点 | 可审查的交付物 |
|---|---|---|
| B0 | Workbench 布局/数值输入、浏览器夹具、插件构建断言、发行物核对 | 缺陷复现 → 修复 → 定向与浏览器整组通过；制品取回记录 |
| B1 | 端口缺口清单；`rph/packages/cli`、`dsh-bundle`；Services/MCP 最小补口 | 正式 bin 与 doctor 可用，singlepoint 同链工程回执 |
| B2 | xtb_optimize 所需 attempt 链、便携 skill、同链故障验收 | 第一阶段本地完整回执；LSF 支持另列；没有 fixture parts 的安装后验收 |
| B3 | 有限科研决策、ResearchRecord 的跨任务依赖、optimize/frequency 所需执行链 | 正例/负例/恢复基准；第二 harness 迁移回执 |
| B4 | 构象/热化学扩展、已知 TS/IRC 与 DA 配方逐项补齐 | 适用场景验收；原 G/H 门逐项对账 |
| B5 | 有限专业角色、主管合并、共享研究状态 | 与单 Agent 相同预算的对照结果，收益成立再扩大 |

本表是优先级与依赖建议，不是已授权的实现或发布日期承诺。每批以可运行场景验收；阶段性受限交付不自动满足完整候选发布门。

## 7. 参考与证据入口

- [当前实现与缺口说明](reference/current/ACP_RPH_Status_20261028.md)
- [2026-10-28—29 功能审查及原始证据](../reports/acp2_function_audit_20261028/REPORT.md)
- [四组分架构方案](ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)
- [RPH / Workbench 原实施附件](reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md)
- [MCP / DSH 历史联调手册](reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md)
- [RPH 包与装配说明](../rph/README.md)
- [当前机器覆盖台账](reference/next-change/acp2-contracts/release/evidence-coverage.current.json)
- [独立操作者第二轮结果](../reports/acp2_remediation_20261026/m5-03/m503-operator-receipt-round2/RESULTS.md)

2026-10-28 的现状修订未重做真实运行；随后功能审查新增了 Python、真实 DSH SDK、浏览器与打包回归，结果及范围见审查报告。未新增真实模型/QC/LSF 科学提交，未操作生产服务，也未开始上述功能开发。
