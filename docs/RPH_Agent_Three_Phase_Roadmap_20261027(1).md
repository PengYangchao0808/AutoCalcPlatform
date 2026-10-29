# RPH Agent 三阶段 Perspective 与开发路线

初稿：2026-10-27；本次更新：2026-10-29。核查基线：`094a9fa0`（含上一轮 `281884ac` 及后续报告/审查修正）。

定位：ACP 四组分方案的研究 Agent 专项 perspective，讨论产品方向、职责与实施顺序。本文区分当前源码、历史验收与后续建议；不构成发布签字或功能开发授权。本次为源码及仓库记录核查，未重新执行模型、QC、LSF、浏览器或全量测试。

**核心判断：ACP 已从“把计算程序接给 Agent”推进到“向 Agent 提供可发现、受控、可追溯的研究操作”。近期优先改善 MCP，让已有 harness 的用户仅靠公开接口就能方便地完成真实计算和结果消费。** 三阶段仍保留，但第一阶段已有不少代码交付，重点转为正式入口验收、消费体验和身份闭合；第二阶段重点是受证据约束的研究决策；第三阶段以可测量的协作收益为前提。

## 1. 相比上次讨论，起点发生了什么变化

旧版以 `fd2087c7` 审查为起点，认为默认 RPH 产品组件、研究消费入口及 xtb_optimize 执行链尚待补齐。当前代码已发生以下变化，不能继续照搬旧待办。

| 方面 | 当前源码已经具备 | 仍不能据此认定 |
|---|---|---|
| RPH 产品入口 | CLI 默认调用 `createProductParts`，装配模型 Planner、研究 writer、证据/预算 reader、提交映射与恢复；保留测试注入接口 | 安装后的 bin 已完成真实模型＋QC 同链验收；配置完即可处理任意研究问题 |
| 研究消费面 | 在既有 proposal/approval/wait/snapshot/resume 之外，增加模型、记录、证据准备/读取、预算、checkpoint、readiness 操作 | 所有 harness 已兼容；工具存在即证明持久化、恢复和科学消费整链已通过 |
| 执行入口 | 默认 envelope 为 `singlepoint`、`xtb_optimize` 与已注册 recipe；产品映射支持前两种普通工作流 | optimize/frequency 等全部普通入口已统一；完整 DA 配方已通过 |
| MCP 自描述 | capabilities 可按 workflow 查询提交契约、示例、资源与限制；Confsearch/BatchOptimize 已有共用 preflight/submit 校验 | 15 个工作流都有完整 schema；当前两项返回 `coverage=partial`，其他项为 `not_provided` |
| 状态与产物 | status 增加 workflow、进度、受限产物描述；prepare 产生可读引用，read 校验 generation/digest | 描述符就是 ArtifactRef；旧 manifest 与当前 attempt 的归属已完全证明；生产 SDK 整链已验收 |
| 正文与方法信息 | 完整正文在截断前执行内容检查；不可验证编码被拒；CENSO 信息向报告传递并区分请求/执行信息 | 所有二进制工件均可读；文件摘要一致就能证明本次科学执行 |
| 基线缺陷 | Workbench 布局、数值编辑、查看器及测试隔离已有相关代码修改 | 旧审查所有失败已在当前 HEAD 整组复验并关闭 |

依据：[CLI 装配](../rph/packages/cli/src/compose.ts)、[产品组件和映射](../rph/packages/dsh-bundle/src/product-parts.ts)、[消费操作](../src/acp_services/entrypoints/research_consumption.py)、[执行范围](../src/acp_services/runtime/execution_chain.py)、[工作流契约](../src/acp/api/workflow_contracts.py)、[产物读取](../src/acp_services/data/artifacts.py)。

需要特别保留三个判断边界：

- **产品装配已写入代码，真实科研闭环仍需证明。** M4-02 的模型分支使用 fake 科学派发，真实 QC 在独立分支执行；不能拼成同一研究轨迹。
- **信息更丰富，科学可信度尚未全部闭合。** 当前 CENSO 报告的 `_execution_verified` 明确返回 False；`execution_facts_well_formed` 仅表示形状/文件摘要检查，不能作为执行已验证或命题 supported 的依据。
- **文档也有版本差。** [旧现状说明](reference/current/ACP_RPH_Status_20261028.md)、[RPH README](../rph/README.md)仍描述默认 parts 缺失；[产品入口说明](reference/next-change/acp2-contracts/experience/rph-product-entry.v1.md)仍主要描述 singlepoint，且消费工具数量滞后。P3/P4 历史 PASS 标题、后续审查和当前实现必须按 revision 与范围分别阅读。

## 2. 产品定位：ACP 提供研究操作，RPH 组织研究决策

用户最终需要的不是一次工具调用成功，而是：给出问题与结构后，知道平台能做什么、哪些科学选择需要确认、计算进行到哪里、结果支持什么，以及下一步为什么值得做。

因此产品体验应围绕四个连续问题设计：

1. **能否做？** 发现工作流、输入格式、方法适用范围、资源和校验覆盖；缺信息时补充输入，不靠猜字段提交。
2. **是否应该做？** 明确计算所检验的命题、预期产物、成本与停止条件，适用时获得审批。
3. **实际做了什么？** 用任务、attempt、冻结输入、方法与原生产物回答，区分请求配置和执行事实。
4. **据此下一步做什么？** 对证据作有范围的解释，继续、修订或停止，保留 inconclusive/not_evaluated/refuted。

四组分继续保留：CCCP 是科学实现根，ACP CLI/Services 是执行与平台权威，Workbench 提供结构/任务/证据/审批交互，RPH 提供研究循环。科学执行四层仍为 `cccp/qc → cccp/backends → cccp/calculation → acp`；新科学计算能力进入 CCCP 任务层。

**近期方向已明确：优先服务已有 harness 的用户，先把 MCP 做好用；RPH 专用循环作为后续产品目标，共用同一套平台契约。**

| 使用方式 | 用户获得什么 | 主要责任 |
|---|---|---|
| 通用 harness＋便携 skill＋ACP MCP | 在已有 Agent 会话中完成发现、确认、提交、取回和解释 | harness 管理对话；skill 指导调用；ACP 保证授权、执行与事实 |
| RPH 专用研究循环 | 持久研究记录、受限多轮决策、重启续接与预算约束 | RPH 组织研究策略；通过相同 MCP 操作消费平台能力 |

这是后续设计建议，便携 skill 和第二 harness 尚无交付证据。DSH 继续作为参考实现，`rph research/resume` 是专用循环的参考入口；无需要求所有使用者先迁移到 RPH，也不另建浏览器 Agent 状态机。Workbench 应能让用户检查同一研究的计划、任务、证据与人工决定。

## 3. 三阶段目标与当前距离

| 阶段 | 可交付体验 | 当前进展 | 下一出口 |
|---|---|---|---|
| 一：可靠使用平台 | 给定结构/方法/预算，完成自然语言→发现/确认→真实计算→引用结果；断线后找回 | 默认装配、singlepoint/xtb_optimize 映射、MCP 消费与工件链已有代码 | 安装后正式入口同链真实验收；恢复与取消负例；便携 skill；明确可支持范围 |
| 二：有限自主科研 | 给定候选结构，优化→频率检查→依据证据继续、修订或停止，再扩展排序与路径验证 | Supervisor、ResearchRecord、预算/审批、bounded DAG 和部分真实科学链 | 跨任务证据绑定、有限策略修订、正负例基准；第二 harness 迁移 |
| 三：有收益的多 Agent | 专业角色提出方案或独立审查，由主管合并与授权 | 可复用平台与研究契约；尚无协作交付证据 | 同预算对照证明质量/效率收益，再增加角色与共享假设图 |

四种 skill 是职责划分，不等于四个专业 Agent。当前生产组合要求模型 Planner，其他 helper 默认确定性；后续按需要增加科研理解、假设和审查能力，不以模型调用数或角色名称数量衡量进步。

第一阶段优先验收已有 harness 经 MCP 完成真实任务，不以 RPH 专用入口就绪为前置。RPH 的正式装配和恢复另行验收，不重复建设平台能力。第二 harness 测的是可迁移性，多 Agent 测的是协作收益，两者分别验收。

## 4. 通信、状态与证据的边界

```text
通用 Harness/便携 skill ─┐
RPH 研究循环 ──────────┴→ MCP stdio → Controller Application API
Workbench ────────────────────────→ 同一 Controller
                                     → Services → CCCP/Worker → 本地或 LSF
```

入口继续为 `acp mcp serve --controller <url>`。MCP 适配器可随会话启停，任务由 Controller 持有。HTTP/SSE 的存在不表示 MCP Streamable HTTP 已实现。RPH 依赖方向保持 `cli → dsh-bundle → bridge → research`；bridge 不直接 HTTP/SSH、不起 QC、不写任务数据库。

当前未协商会话有八个基础工具。协商 `acp.research.v1` 后有既有十个研究工具；消费后端装配时还开放十一项消费操作，即当前完整装配可见 **8＋10＋11**。依据为 [visible_tool_names](../src/acp_services/entrypoints/mcp.py) 与 [CONSUMPTION_ROUTES](../src/acp_services/entrypoints/research_consumption.py)，不能继续把 18 当固定总数。客户端须协商后重新 tools/list，并检查需要的实际工具；数量本身不是兼容性保证。

下一步应遵守以下状态规则：

- 任务事实归 ACP，研究问题/计划/决策经平台资源与 revision/CAS 持久化。聊天摘要不能替代研究记录。
- 恢复先对账已有 proposal、任务、attempt 和证据，再决定是否重新规划。重试复用原幂等键；计划/输入改变则形成新身份。
- status 的产物描述只供发现，prepare 后才取得可读 ArtifactRef。缓存未知须明确表达，轮询不能隐式下载远端结果。
- 科学证据至少区分“已发现”“可读且摘要一致”“属于当前执行”“足以支持命题”。这些是讨论中的语义层次，不在本文新增状态枚举。
- 长任务采用有界 wait/事件或限频查询，不让等待持续消耗模型调用。token/core-hours 累计硬强制仍 DEFERRED，统计值和客户端停止规则不得冒充平台硬上限。

**一个新增优先问题是跨会话找回。** 已知 proposal/TaskRef 的 resume 不等于用户忘记引用后仍能找到研究。建议补充按项目与权限限定的研究/任务查询，支持分页和有限筛选；是否新增工具由用例与兼容设计决定，不能为了维持工具数量而让 Agent 猜 ID 或读数据库。

## 5. 科学与验收判断

真实科研轨迹必须同时保留：输入和模型计划、适用的审批、TaskRef/attempt/release、冻结科学配置、原生产物与摘要、验证结果、后续决定和停止原因。同链中的实际模型与实际 QC 不能被不同分支的记录替代。

科学评价允许正确拒绝或停止。已有 DA route-a 执行 PASS，但端点关联命题 refuted，平台/RPH 要求 revise；计算级别偏差、Local 专属回执、计时基准和 DA-ENV-LSF 仍须分别处理。普通跨 TaskRef 循环不能替代完整单 TaskRef DA 配方验收。

[MCP UX P3](../reports/acp2_mcp_ux_20261029/P3_ACCEPTANCE.md)记录了 scripted Controller 的提交→发现→prepare→read fixture；真实 SDK/生产 manifest→prepare→store 同链仍未闭合。[P4](../reports/acp2_mcp_ux_20261029/P4_ACCEPTANCE.md)有后续降级说明；当前源码不声称 CENSO 执行 verified。[第二轮审查](../reports/acp2_mcp_ux_20261029/ASTRA_FIX_VERIFICATION_2.md)针对 `3cba191c`，不能直接充当 `281884ac` 的验收结果。

正式[机器台账](reference/next-change/acp2-contracts/release/evidence-coverage.current.json)仍有 G05/G10/G13/G15/G18、H3/H4/H7 等 OPEN，`candidate_full.allowed=false`。M5-03 第二轮独立安装通过不关闭这些聚合门。旧 BOM 的本机可取回性问题需在新候选上明确对账，不能把重构建称为恢复同一制品。

后续每个交付应分别说明源码实现、fixture 回归、实际 SDK、真实科学与安装后产品验收，引用确切 revision。历史日志和审查意见不自动成为当前 PASS；本次仅核查文本和源码，不补写运行结果。

## 6. 审查后的开发建议（2026-10-29）

旧顺序是“基线修复→补产品装配→真实闭环”。现在更适合调整为：**对账最近 MCP 改动→已有 harness 真实使用闭环→补齐消费体验→RPH 有限科研循环→评估多 Agent**。原 B0/B1/B2 中已落代码的部分不再从零实施。

### 6.1 先建立一个可信、可安装的当前候选

核对已经修改的 Workbench、产品装配、消费工具、映射与执行入口；对实际受影响范围重跑回归，记录未通过和未验证项。同步 README、产品入口文档、工具说明与测试所指的 revision，保留旧报告的历史范围。

优先解决真实场景上的阻塞：产物 attempt 归属、证据可读性和科学归因、审批续接与恢复。全仓格式化、所有 DEFERRED 能力及完整 DA 放行不作为这条有限场景的前置。

### 6.2 第一条产品链：已有 harness 直接使用 MCP

以一个用户已经使用的 harness 为入口，固定一个小分子、明确输入/电荷/多重度/方法/预算，通过公开 MCP 完成发现、预检、提交、观察与结果读取。singlepoint/xtb_optimize 可作为小型场景；最近重点修改的 Confsearch/BatchOptimize 另选代表请求验证参数发现和结果消费。先完成本地场景，再按同契约验证 LSF，并单独声明支持范围。

验收至少包含：真实模型计划→平台预检/审批→真实 QC→原生证据→有引用的解释或停止；同时覆盖适用的审批续接、MCP 断开、harness 会话重启、重试不重复提交、取消/超时、旧 attempt 证据拒绝、证据缺失和非法计划。不能靠读源码、手工补服务器路径或伪造产物引用完成验收。RPH 进程重启与专用 checkpoint 恢复在其产品批次单独验证。

本批的产出应让用户回答：如何启动，何时需要确认，如何看进度，退出后如何继续，结果不足时会发生什么。用户应确认科学选择，Agent 应能从发现和预检结果获得下一步所需引用，而非要求用户手填内部 digest。简化交互时保留服务端身份绑定。

### 6.3 让通用 Agent 可靠消费 ACP

以实际错误案例为顺序补契约：先覆盖下一研究循环需要的 singlepoint/xtb_optimize/optimize/frequency，再扩展其他工作流。Confsearch/BatchOptimize 的 partial 也需按消费分支逐项收敛，不能仅修改 coverage 标签。

优先交付便携 skill、版本化示例、跨会话查询和有限结果摘要。skill 说明发现→确认→预检→提交→等待→证据→停止规则，科学事实仍由平台提供。大工件分页或专用二进制读取按真实需求设计，不通过放宽正文策略解决。

用第二 harness 执行相同输入与负例，检查它是否需要读源码、猜参数、手填服务器路径或绕过 MCP 才能完成任务。如果需要，优先修接口或文档。此处不增加第二任务库或第二执行权威。

### 6.4 第二阶段：把单步结果变成可审查的研究决定

首个场景收窄为给定候选结构的优化→频率检查→依据证据继续、修订或停止。稳定后增加可比方法下的单点/热化学与候选排序，再扩展构象搜索和已知 TS/IRC 验证。自主发现完整反应机理不作为首版出口。

| 层次 | 负责什么 | 下一步如何产生 |
|---|---|---|
| ACP bounded DAG | 提交前确定的计算依赖、参数与产物传递，冻结为可恢复执行单元 | 满足预定义依赖/条件后执行，内部不运行开放式模型策略 |
| RPH 研究循环 | 解释证据、修订问题/方法、选择下一任务或 DAG、决定停止 | 形成新 Proposal/revision，再执行适用预检、审批与预算检查 |

研究状态保存问题/命题、计划 revision、依赖 TaskRef、生产者 attempt、产物 generation/digest、决策理由与停止原因。先扩展已有 ResearchRecord/Proposal；多个候选与依赖确实需要时再扩大假设图。重算导致证据失效须显式处理，不得把旧证据接到新 attempt。

评价基准包含成功、未收敛、虚频不符、证据不足、端点 refuted、预算停止和服务重启。评价正确决策、重复/无效任务、恢复一致性、耗时与费用；不以“最后都 supported”作为成功。

### 6.5 第三阶段：先验证一种协作收益

在单 Agent 基准和第二 harness 稳定后，先试主管＋独立证据审查角色。用相同输入、模型条件和总预算，与单 Agent 比较决策质量、重复计算、时间和费用。收益不足则不扩展。

专业 Agent 提交结构化提案/审查意见，主管合并，平台保持统一授权与预算。之后再按实际瓶颈增加构象、TS、IRC、热化学角色和冲突处理。Raft、多主、Rust 重写不作为阶段前置。

### 6.6 建议的下一批工作

| 顺序 | 工作 | 出口 |
|---|---|---|
| N0 | 当前代码/文档/候选与回归对账，修复场景阻塞 | 可安装版本、清楚的支持矩阵和未验证项 |
| N1 | 已有 harness 经 MCP 的发现→预检→提交→状态→产物闭环 | 一条真实轨迹及负例回执；不依赖 RPH 或源码知识，Local/LSF 分列 |
| N2 | 根据 N1 反馈完善契约、错误、结果摘要与任务找回；便携 skill 和第二 harness | Agent 可发现、可纠错、可取回、可续接 |
| N3 | RPH 正式入口与恢复验收、optimize/frequency 证据链、跨任务研究决策 | 有限科研正负例基准与一致恢复 |
| N4 | 构象/热化学/已知 TS/IRC、DA 配方按范围补齐 | 科学适用范围和原 G/H 门逐项对账 |
| N5 | 单一专业角色试验 | 同预算对照收益，决定是否扩大多 Agent |

本表是讨论建议，不是发布日期或全部实现承诺。N2 的接口设计可与 N1 验收交替推进，以真实使用暴露的问题决定范围。

## 7. 接下来值得讨论的产品选择

**建议优先讨论“第一位用户怎样完成第一项研究”，再决定新增模块。** 可以从三个选择收敛：

1. **主使用方式已确定。** 近期优先已有 harness＋ACP MCP，便携 skill 辅助使用；RPH 专用研究循环后置。下一步需要用实际任务找出最近一波 MCP 改动之后仍存在的交互阻力。
2. **首次科研场景。** 建议用 singlepoint/xtb_optimize 证明工程链，再以“候选优化＋频率判断”检验有限科研能力；若实际需求更偏构象筛选，应据此调整契约与证据优先级。
3. **自主程度。** 建议先允许 Agent 在预先确认的结构、方法范围与预算内决策；改变科学问题、显著升级方法或扩大计算范围时重新确认。具体边界应体现为计划和平台策略，不能只写在提示词里。

这三个选择会决定后续最值得补的是接口覆盖、证据身份还是研究策略；当前不宜仅因已有多个 skill 就优先投入多 Agent。

## 8. 参考与证据入口

- [原现状说明（历史基线）](reference/current/ACP_RPH_Status_20261028.md)
- [功能审查（fd2087c7）](../reports/acp2_function_audit_20261028/REPORT.md)
- [MCP UX 实施计划（历史计划，不作当前完成清单）](reference/next-change/ACP_MCP_UX_Remediation_Plan_20261029.md)
- [P1 校验](../reports/acp2_mcp_ux_20261029/P1_ACCEPTANCE.md)、[P2 发现](../reports/acp2_mcp_ux_20261029/P2_ACCEPTANCE.md)、[P3 产物](../reports/acp2_mcp_ux_20261029/P3_ACCEPTANCE.md)、[P4 方法信息](../reports/acp2_mcp_ux_20261029/P4_ACCEPTANCE.md)
- [产品入口契约](reference/next-change/acp2-contracts/experience/rph-product-entry.v1.md)、[研究消费契约](reference/next-change/acp2-contracts/runtime/research-consumption.v1.json)
- [四组分方案](ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)
- [独立安装第二轮](../reports/acp2_remediation_20261026/m5-03/m503-operator-receipt-round2/RESULTS.md)

本次仅更新 perspective；未改代码、正式门或历史回执，未新增真实模型/QC/LSF 提交，未操作生产服务。
