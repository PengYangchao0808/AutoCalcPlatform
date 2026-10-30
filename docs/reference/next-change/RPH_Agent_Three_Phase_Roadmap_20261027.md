# RPH Agent 三阶段开发大纲
日期：2026-10-27
定位：现有 ACP 架构方案的 RPH 专项附件；仅规划，不表示已实施或真实联调通过。
目标仓库：E:/Calculations/Common_Script/Auto_Calc_Platform/ACP_V1_20260811
本次源码观察：HEAD 484764f6；未冻结工作区快照，不作为正式验收 revision。

## 1. 对齐已有讨论与代码
已读取聊天“完善RPH架构设计”“设计ACP插件化架构”“ACP CLI Raft同步方案”“ACP Rust 重构方案”中的可访问消息，以及“拆分前端界面与架构调整”的近期内容。部分历史回答仅返回引用标记，未当作已读全文。
确认的需求：skills＋MCP 调用 ACP；保留 CCCP 底座和集群能力；兼容不同 harness；后续考虑 Rust；RPH 与 Workbench 并列接入平台。
这里的“同步聊天”是提炼需求，不是后台持续同步，也不把聊天内容直接作为 ResearchRecord。

现有四组分：CCCP、ACP CLI/Services、Workbench、RPH。
另有科学执行四层：cccp/qc → cccp/backends → cccp/calculation → acp。两种划分并存，不新增平行执行根。
现有 rph 已包含 research / bridge / dsh-bundle / cli，且已有 supervisor、四种科研 skill、研究记录与协议映射。阶段工作以补齐和验收为主。
现有 acp mcp serve --controller <url> 入口绑定八个基础工具；B26a 历史探针记录握手与发现成功，不能据此认定真实模型＋QC＋LSF 全链路通过。本次未重跑这些验收。

## 2. 三阶段路线
| 阶段 | 开发重点 | 代表场景 | 进入下一阶段的条件 |
|---|---|---|---|
| 第一阶段：skills＋MCP 交互闭环 | 一个便携科研 skill；复用现有 MCP；真实 harness 接线；固定输入、方法与预算 | 自然语言请求 → 能力发现 → 预检 → 提交一个 xtb_optimize → 查询 → prepare/read → 输出带任务与工件引用的结果 | 至少一个 harness＋真实模型＋真实科学程序通过；同一任务在 CLI/Workbench 可见；重连不重复提交；取消可追踪 |
| 第二阶段：单 Agent 科研闭环 | 用一个 Supervisor 调用 interpreter/planner/reviewer/synthesizer；补齐研究恢复、证据判断和有限策略调整 | 对一个小体系完成构象筛选 → 优化 → 频率 → 单点/热化学；再以适用体系扩展到候选 TS/IRC | 多步骤、受控失败恢复、预算/轮次停止和研究记录恢复通过；既有科学验证器给出证据；本地与 LSF 分别留证 |
| 第三阶段：多 Agent 机理研究 | 主管统一调度，专业 Agent 产生结构化提案；共享假设图与证据；统一预算和记录 | 问题/文献与输入整理 → 竞争机理 → 路径与 TS → 频率/IRC → 可比自由能与选择性 → 审查报告 | 在约定反应范围内完成至少一个端到端真实研究案例；可回放每个结论、任务及原始工件；冲突、重复任务、暂停恢复可控 |

第一阶段先选 DSH 作为参考接入，再用第二个 harness 验证迁移。MCP 连接兼容与自主科研行为通过分别登记。
第二阶段的多种 skill 仍由一个 Agent 顺序调用。TS/IRC 仅在能力注册和适用性允许时启用；缺能力则登记 CCCP 研发任务。
第三阶段以限定体系与证据标准定义“完整”，对缺失验证保留 inconclusive/not_evaluated。

## 3. 核心代码组成
沿用当前依赖：cli → dsh-bundle → bridge → research。
| 位置 | 核心责任与下一步 |
|---|---|
| rph/packages/research | ResearchRecord、Proposal、证据引用、记忆；增量表达假设图/研究检查点，不持有任务数据库 |
| rph/packages/bridge | 统一科研操作、TaskRef、错误和扩展协商；保持 PlatformClient 注入，内部不放 HTTP/fetch/进程执行 |
| rph/packages/dsh-bundle/skills | 复用四种 skill 与 Supervisor；第一阶段整理便携 SKILL.md/共享提示与 schema，第二阶段补齐科研循环 |
| rph/packages/dsh-bundle | DSH 专用配置和装配；其他 harness 增加薄适配包，端口契约统一，不复制科研逻辑 |
| rph/packages/cli | 配置检查、研究入口、恢复与报告引用；不启动第二 Controller 或科学程序 |
| src/acp/services_mcp_bootstrap.py | 现有 MCP 与 Controller 的装配根；继续复用规范化请求 |
| src/acp_services | 身份、预算、持久状态、事件、研究资源、工件和集群执行平台 |
| src/cccp/calculation/tasks | 新科学能力唯一执行根；RPH 的能力缺口在此补齐并注册 |
| rph/test、tests、scripts/acceptance | 协议/隔离回归、Agent 轨迹回放与真实计算验收 |

便携层定义目标、步骤、输入输出及停止条件；harness 适配层处理 skill 加载格式、MCP 配置、模型会话和工具命名。
跨 harness 的长期研究状态从平台 ResearchRecord 恢复，不依赖聊天会话搬运。当前 dsh-bundle 的 TypeScript skill/schema 不能直接视为所有 harness 可加载的 skill 包。

## 4. ACP–RPH 协同通信
逻辑路径：
用户 → Harness/RPH → MCP 适配器 → ACP Controller → Services/科学工作流 → CCCP/Worker → 本地软件或 LSF。
Workbench 并列连接同一 Controller，观察相同任务、工件和研究记录。

第一阶段采用现有本机 stdio MCP：
Harness → acp mcp serve --controller <url> → Controller HTTP Application API。
MCP 适配器可随 harness 启停；已提交任务由 Controller 持续管理。远端集群连接由 ACP 持有。

八工具复用：
- acp_capabilities：发现真实可用能力及协议扩展。
- acp_preflight：检查科学输入、参数和平台条件。
- acp_submit / acp_status / acp_cancel：提交、观察、取消。
- acp_prepare_artifact / acp_read_artifact：显式准备与只读获取工件。
- acp_analyze：消费正式分析与证据输出。

通信约定：
1. 提交快速返回持久 TaskRef；长计算使用平台事件/有界 wait，基础路径可限频查询，不持续消耗模型调用。
2. 复用现有 acp.research.v1 映射并先协商，不能把桥接操作名当成服务器已公布工具。
3. 同一科学请求重试复用幂等键；输入或计划改变须更新身份和幂等键。记录 job_id、attempt、release_id；工件另绑定 generation/digest。
4. 任务状态归 ACP；科研轮次由 Supervisor 编排并通过平台研究资源保存。ResearchRecord 更新用 revision/CAS，冲突后重读合并。
5. 工具返回结构化状态、错误、引用；大日志/轨迹保留为工件，模型按需读摘要与相关片段。
6. 多 Agent 通过结构化提案和证据引用协作；主管统一合并研究记录与提交决策。并行计算仍走 ACP 的准入、去重和预算。

后续若确需远程 MCP 接入，再单独验证 Streamable HTTP；现有 Controller HTTP/SSE 不等于 MCP HTTP 已实现。
当前先采用单 Controller 权威状态；Raft/多主与 Rust 重构不作为三阶段前置。未来替换平台内部实现时，优先保持 MCP、Application API 与科学契约兼容。

## 5. ACP–RPH 同步迭代与进化
研究闭环：
提出问题 → 形成假设 → 规划 → ACP 执行 → 科学证据 → 接受/修订/停止。
任务 completed 与机理 supported 分开判定；科学结论由适用验证器和工件支持。

开发闭环：
失败案例 → 定位到 skill / bridge / Services / CCCP → 建立回归案例 → 修复 → 共同候选验收 → 发布可回退版本。
RPH 产出结构化缺陷/改进提案；开发代理在隔离开发环境修改与测试，运行中的科研 Agent 不自行修改生产实现。

每轮绑定 ACP revision、RPH revision、契约/skill 版本、harness/模型、科学软件和测试集版本。
评价至少区分：协议通过率、流程完成率、科学验证通过率、恢复正确性、模型/QC 成本；用固定小型基准比较升级前后。
第一阶段验证接口和一项真实计算；第二阶段加入科学链与故障回放；第三阶段再检验多 Agent 协作收益和端到端证据完整性。
只有有证据的经验进入研究记忆；skill/recipe 升级经回归后晋升，不把一次成功自动推广到其他体系。

## 6. 第一批实施顺序
1. 冻结小型输入、当前候选、模型/QC 预算与隔离测试 Controller。
2. 整理一个便携 skill，接通现有八工具，完成一个真实 xtb_optimize 闭环。
3. 核对 CLI/Workbench 同任务展示、重复提交保护、断线重连和取消。
4. 保存任务、工件与测试证据，形成第一阶段通过/失败/未验证矩阵，再进入第二阶段。

参考：
- [现有四组分总方案](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)
- [RPH 实施附件](ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md)
- [MCP/DSH 联调手册（历史时点说明需结合当前代码）](ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md)
- [MCP 2025-06-18 传输规范](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)

本附件没有修改代码、数据库、配置或服务，没有执行科学任务，也不将历史测试状态提升为本次验收通过。

