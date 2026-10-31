# reference/next-change — 下一轮方案与在役契约

本目录存放**尚未实施、或正在实施**的方案，以及已落地但仍被代码引用的契约。判断"在不在用"看引用关系，不看写作日期。

状态图例：

| 状态 | 含义 |
|---|---|
| **现行** | 正在推进的路线，实施顺序以它为准 |
| **在役** | 已实施或已被代码/测试引用，改动需同步代码 |
| **记录** | 已完成批次的计划/验收记录，保留追溯，不构成待办 |
| **机器** | 机器校验产物，**禁止手改** |

> 一份方案一个文件：进展、切片验收与修订追加到同一文件的新小节，**不另建并行总方案**。
> 被取代的稿子移入 [`archive/`](../../archive/)；本批（2026-10-31）已归档 2026-10-16/18/20 三代被取代的稿子与评审轮次。

## ACP 3.0（现行，2026-10-31）

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_3.0_Architecture_20261031.md](ACP_3.0_Architecture_20261031.md) | 以用户虚拟工作站为中心的 RPH/DSH 一体化体系；后端/前端两计划的共同上位 | 现行 |
| [ACP_3.0_Backend_Plan_20261031.md](ACP_3.0_Backend_Plan_20261031.md) | 后端实现计划（Control Plane / CCCP / RPH） | 现行 |
| [ACP_3.0_Frontend_Plan_20261031.md](ACP_3.0_Frontend_Plan_20261031.md) | 前端兼容与实施计划（Workbench on DSH） | 现行 |
| [ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md) | 一人一 Linux 虚机 + 一人一 DSH + 虚拟文件系统的代码级差距与文件清单 | 现行（附件） |

## 前端（2026-10-27…31）

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Frontend_Target_Workbench_Plan_20261030.md](ACP_Frontend_Target_Workbench_Plan_20261030.md) | 目标形态方案（对齐样板图；Ketcher / DSH 面板） | 现行 |
| [ACP_Frontend_UI01_Shell_Design_20261030.md](ACP_Frontend_UI01_Shell_Design_20261030.md) | UI-01 Shell 设计规格（`frontend/next/index.html`） | 在役；被 4 处前端代码引用 |
| [ACP_Frontend_UI00_Module_Recipe_Contract_Draft_20261030.md](ACP_Frontend_UI00_Module_Recipe_Contract_Draft_20261030.md) | 模块/工作区配方契约草案（`schemaVersion: 0-draft`） | 在役（draft） |
| [RPH_Agent_Three_Phase_Roadmap_20261027.md](RPH_Agent_Three_Phase_Roadmap_20261027.md) | RPH Agent 三阶段开发大纲 | 现行 |
| [ACP_Frontend_UI00_DSH_Capability_Baseline_20261030.md](ACP_Frontend_UI00_DSH_Capability_Baseline_20261030.md) | UI-00 基线：DSH 能力核验与旧入口事实清单 | 记录 |
| [ACP_Frontend_UI01_UI02_UI03A_Closure_20261030.md](ACP_Frontend_UI01_UI02_UI03A_Closure_20261030.md) | UI-00 → UI-03A 收口 | 记录；`frontend/js/modules/task-config/model.js` 引用 |
| [ACP_Frontend_UI03_UI04_UI05_Closure_20261030.md](ACP_Frontend_UI03_UI04_UI05_Closure_20261030.md) | UI-03 / UI-04 / UI-05 收口 | 记录 |
| [ACP_Frontend_Review_Layout_Remediation_20261030.md](ACP_Frontend_Review_Layout_Remediation_20261030.md) | 新前端复核与布局修正 | 记录 |
| [ACP_Frontend_差距与修正_20261030.md](ACP_Frontend_差距与修正_20261030.md) | 新旧界面差距与本轮修正 | 记录 |

## MCP / Agent（2026-10-29—30）

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md](ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md) | 统一实施计划：MCP 易用性 U01—U09、科研指导、15 个 active 工作流说明与自动编排 prompt | **现行**；计划中的工具/指南内容包尚未实现 |
| [ACP_MCP_Discovery_Bounded_Plan_20261030.md](ACP_MCP_Discovery_Bounded_Plan_20261030.md) | 有界发现与科学提示（第 4 轮实施计划） | 现行 |
| [ACP_MCP_Guidance_P0_Baseline_20261030.md](ACP_MCP_Guidance_P0_Baseline_20261030.md) | 指南与自动科研链 P0 基线与首批切片交付 | 记录 |
| [ACP_MCP_UX_Remediation_Plan_20261029.md](ACP_MCP_UX_Remediation_Plan_20261029.md) | MCP 可用性修复计划（10-29） | 记录 |

> 计划中标注"尚未实现"的能力，不得因文档列名就调用或宣称可用；权威仍是 `mcp.py::visible_tool_names` 与 `research_consumption.py::CONSUMPTION_TOOL_NAMES`。

## ACP 2.0 计划与发布（2026-10-20…23）

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_2.0_Parallel_Execution_Index_20261020.md](ACP_2.0_Parallel_Execution_Index_20261020.md) | 三 Agent 并行执行总索引（范围 / Owner / 阶段依赖） | 记录 |
| [ACP_2.0_Parallel_Plan_A_CCCP_20261020.md](ACP_2.0_Parallel_Plan_A_CCCP_20261020.md) | 计划 A：CCCP 科学契约、能力与证据 | 记录 |
| [ACP_2.0_Parallel_Plan_B_Runtime_20261020.md](ACP_2.0_Parallel_Plan_B_Runtime_20261020.md) | 计划 B：平台执行、MCP、服务器环境与集成发行 | 记录 |
| [ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md](ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md) | 计划 C：RPH、Workbench 与本服务器体验验收 | 记录 |
| [ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md](ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md) | MCP / DSH 本服务器联调与验收手册 | 在役（可复跑手册） |
| [ACP_2.0_Release_Version_List_20261023.md](ACP_2.0_Release_Version_List_20261023.md) | 2.0 四发行版本清单（B25b） | 记录 |
| [ACP_Architecture_Acceptance_Remediation_Plan_20261020.md](ACP_Architecture_Acceptance_Remediation_Plan_20261020.md) | 架构调整验收整改计划 | 记录 |

> 2.0 的逐轮评审（2026-10-21/22）已归档：[archive/2026-10-31/history/](../../archive/2026-10-31/README.md)。

## 2026-10-18 三路并行计划

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Parallel_Plan_1_Frontend_20261018.md](ACP_Parallel_Plan_1_Frontend_20261018.md) | 计划一：Frontend 收口与真实能力接入 | 记录；`tests/baseline/cccp-evidence/` 引用 |
| [ACP_Parallel_Plan_2_Services_20261018.md](ACP_Parallel_Plan_2_Services_20261018.md) | 计划二：Services 平台服务与交付 | 记录；`tests/test_f4_scope_audit.py` 引用 |
| [ACP_Parallel_Plan_3_CCCP_20261018.md](ACP_Parallel_Plan_3_CCCP_20261018.md) | 计划三：CCCP 科学闭包、Analysis 与独立发行 | 记录；6 处测试证据引用 |
| [ACP_Architecture_Progress_and_Parallel_Index_20261018.md](ACP_Architecture_Progress_and_Parallel_Index_20261018.md) | 计划完成情况与三路并行索引 | 记录；`tests/test_services_first_slice_contracts.py` 引用 |

> 本轮次的评审 R1—R5 已归档：[archive/2026-10-31/history/](../../archive/2026-10-31/README.md)。

## Services 契约（在役）

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Services_First_Slice_Contracts.md](ACP_Services_First_Slice_Contracts.md) | 首片契约冻结（B00-3 / todo 3） | 在役；`src/acp_services/contracts/*`、`data/contracts.py`、`runtime/freshness.py` docstring 引用 |
| [ACP_Services_Component_Manifest.md](ACP_Services_Component_Manifest.md) | 组件清单：现有 / 候选 / 试点 / 批准 | 在役；`workspace/identity_lifecycle.py` 引用 |
| [ACP_Services_Standalone_Recipe.md](ACP_Services_Standalone_Recipe.md) | 可复现安装配方与 capability 报告（B41a+B41b） | 在役；`tests/test_services_standalone_recipe.py`、`test_services_b41b_defect_fixes.py` |
| [ACP_Services_Identity_Support_Scope.md](ACP_Services_Identity_Support_Scope.md) | 身份支持范围与能力声明 | 在役；`runtime/lab_capabilities.py`、`workspace/identity_lifecycle.py` |
| [ACP_Services_MCP_Client_Matrix.md](ACP_Services_MCP_Client_Matrix.md) | MCP thin entry 协议 / 客户端矩阵（B08-2） | 在役；`tests/test_services_doc_sync.py`（状态块） |
| [ACP_Services_Metering_Policy.md](ACP_Services_Metering_Policy.md) | 计量政策（冻结契约 B10-1） | 在役；同上 |
| [ACP_Services_Diagnostics_Contract.md](ACP_Services_Diagnostics_Contract.md) | 结构化日志与诊断导出契约（B08-6） | 在役；同上 |
| [ACP_Services_Freshness_Contract.md](ACP_Services_Freshness_Contract.md) | Freshness / Availability 契约（B03-2） | 在役；同上 |
| [ACP_Services_Events_A04_Contract.md](ACP_Services_Events_A04_Contract.md) | Event Stream A04 消费契约（DEFERRED） | 在役；同上 |

> 这 9 篇里，**6 篇**（`Component_Manifest`、`Diagnostics_Contract`、`Freshness_Contract`、`Identity_Support_Scope`、`MCP_Client_Matrix`、`Metering_Policy`）必须恰好带一处 `<!-- SERVICES_STATUS_SYNC:todo70 -->` 状态块（含 `delivered` / `verified` / `NOT_VERIFIED` / `limits`，并指向 `reference/current/ACP_Services_Closure_20261019.md`）；**3 篇**（`First_Slice_Contracts`、`Events_A04_Contract`、`Standalone_Recipe`）**不得**带该状态块。改这些文件前先读 `tests/test_services_doc_sync.py`。

## 专项核验

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Frontend_FE10_Dihedral_Verification_20261016.md](ACP_Frontend_FE10_Dihedral_Verification_20261016.md) | 两套二面角函数的语义差异核验 | 在役；`frontend/js/module_tests/fe10_dihedral_golden.test.js` 引用 |

## 子目录

| 路径 | 内容 | 状态 |
|---|---|---|
| [`adr/`](adr/ADR-003-taskref-evidence-decision.md) | 架构决策记录：[ADR-003](adr/ADR-003-taskref-evidence-decision.md) TaskRef 归一 + digest-bound 证据投影、[ADR-006](adr/ADR-006-mcp-negotiation.md) MCP 握手协商、[ADR-008](adr/ADR-008-limited-diels-alder-criteria.md) 限定 DA 科学判据 | 在役（决策记录，改实现前先读） |
| [`acp2-contracts/`](acp2-contracts/manifest.json) | ACP 2.0 机器契约包：`science/`、`runtime/`、`experience/` 的 schema、manifest、摘要、验收样本 | **机器**；`scripts/acceptance/validate_acp2_contracts.py` 校验，禁止手改 |
| [`examples/`](examples/acp_mcp_stdio_probe.py) | 被 `scripts/acceptance/*` 调用的探针脚本 | **机器**；路径被验收脚本引用 |
