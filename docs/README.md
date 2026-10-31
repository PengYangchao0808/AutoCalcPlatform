# ACP 文档入口

> **最后核对**：2026-10-31（文档结构批次）· 本文件是唯一入口。新增、移动或废弃文档时，必须同步本文件与对应目录的 `README.md`，并跑 `python scripts/check_docs_links.py`。
>
> 代码契约以仓库根的 `AGENTS.md` 和各包 `AGENTS.md` 为准；两者冲突时**以代码与 `AGENTS.md` 为准**，文档按它修正。

## 0. 当前状态与最新审查（2026-10-31 合并 origin/main 后）

- **完整功能审查**（执行跨 2026-10-28—29，审查基线 `fd2087c7`）：[reports/acp2_function_audit_20261028/REPORT.md](../reports/acp2_function_audit_20261028/REPORT.md) —— 记录测试失败、代码缺口与未验证项；不自动关闭发布门。
- **ACP / RPH 现状说明**（10-28）：[reference/current/ACP_RPH_Status_20261028.md](reference/current/ACP_RPH_Status_20261028.md)。
- **MCP 使用指南**：[根 README §MCP 使用指南](../README.md#mcp-使用指南)。
- **RPH 三阶段路线图**：[RPH_Agent_Three_Phase_Roadmap_20261027(1).md](<RPH_Agent_Three_Phase_Roadmap_20261027(1).md>)（文件名含 `(1)`，链接必须用尖括号）。
- **下一批 MCP / Agent 开发计划**（计划中的工具与指南**尚未实现**）：[reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md](reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md)。

> **完整 ACP2 放行仍为 `NOT_VERIFIED`。** 独立安装通过、科研闭环通过分别记录，不能相互替代；`NOT_VERIFIED` 不得改写成 PASS。

## 1. 按任务找文档

| 你要做的事 | 先读 | 再读 |
|---|---|---|
| 改计算任务核心 / 新增任务类型 | 根 `AGENTS.md`（四层架构、ANTI #17） | [ACP_CCCP_Task_API_DevDoc](reference/current/ACP_CCCP_Task_API_DevDoc.md) |
| 改工作流、调度、Job 生命周期 | `src/acp/AGENTS.md`、`src/acp/scheduler/AGENTS.md` | [ACP_Job_File_Layout_Spec](reference/current/ACP_Job_File_Layout_Spec.md) |
| 改平台服务层（store/data/runtime） | `src/acp_services/AGENTS.md` | [ACP_Services_Closure_20261019](reference/current/ACP_Services_Closure_20261019.md) |
| 做前端可视化（3D/曲线/帧/振动/谱图/网络） | [ACP 3.0 前端计划 v2（可视化先行）](reference/next-change/ACP_3.0_Frontend_Plan_20261031.md) | [能量/轨迹查看器 DevDoc](reference/current/ACP_Energy_Trajectory_Viewer_DevDoc.md)、[结构查看器 DevDoc](reference/current/ACP_Structure_Viewer_DevDoc.md) |
| 改 API / 前端接口 | `src/acp/api/AGENTS.md` | [任务文件布局](reference/current/ACP_Job_File_Layout_Spec.md)、[前端目标形态方案](reference/next-change/ACP_Frontend_Target_Workbench_Plan_20261030.md) |
| 改结构输入 / 结果复用 / 查看器 | [ACP_Structure_Sources_DevDoc](reference/current/ACP_Structure_Sources_DevDoc.md) | [ACP_Structure_Viewer_DevDoc](reference/current/ACP_Structure_Viewer_DevDoc.md)、[能量与轨迹查看器](reference/current/ACP_Energy_Trajectory_Viewer_DevDoc.md) |
| 改 NMR / DP4 | `src/acp/nmr/AGENTS.md` | [ACP_NMR_DP4_DevDoc](reference/current/ACP_NMR_DP4_DevDoc.md) |
| 改 MCP / Agent 接口与指南 | `src/acp_services/AGENTS.md`（MCP 段） | [MCP / Agent 统一计划](reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md) |
| 了解当前真实状态（审查与放行门） | [ACP2 功能审查](../reports/acp2_function_audit_20261028/REPORT.md) | [ACP / RPH 现状说明](reference/current/ACP_RPH_Status_20261028.md) |
| 看下一轮路线 | [ACP 3.0 架构](reference/next-change/ACP_3.0_Architecture_20261031.md) | [3.0 后端计划](reference/next-change/ACP_3.0_Backend_Plan_20261031.md)、[3.0 前端计划](reference/next-change/ACP_3.0_Frontend_Plan_20261031.md) |
| 跑真实计算验收 | [ACP_Function_Test_Manual](reference/current/ACP_Function_Test_Manual.md) | — |
| 查历史决策/旧方案 | [archive/](archive/) | [2026-10-31 批次](archive/2026-10-31/README.md)、[2026-10-16 批次](archive/2026-10-16/README.md) |

## 2. 目录职责（四类，不再新增第五类）

| 目录 | 含义 | 能否直接改 |
|---|---|---|
| [`reference/current/`](reference/current/README.md) | **现行契约与开发参考**：仍约束实际行为的设计、契约与实施/发布记录 | 可以，但要先确认没有测试或源码 docstring 钉住它（见 §4） |
| [`reference/next-change/`](reference/next-change/README.md) | **下一轮方案与在役契约**：尚未实施或正在实施的方案、待接入契约 | 可以；新方案只加在这里 |
| [`reference/next-change/acp2-contracts/`](reference/next-change/acp2-contracts/manifest.json) | **机器契约包**：schema、manifest、摘要与验收样本 | **禁止手改**（按摘要校验，改动会使契约门失败） |
| [`archive/<YYYY-MM-DD>/`](archive/) | **冻结归档**：已退出当前实施入口的批次，按批次记录迁移表 | **禁止改写正文**；仅可追加批注 |

`docs/` 根下只保留下列跨批次文件（其余文档一律进 `current/`、`next-change/` 或 `archive/`）：

- [ACP_Architecture_Adjustment_Plan_20261016.md](ACP_Architecture_Adjustment_Plan_20261016.md)——2026-10 架构调整总方案（含 §45 Services 实施记录；被 `tests/test_services_doc_sync.py` 引用，**路径不可变**）。
- [ACP_2.0_Four_Component_Collaboration_Plan_20261020.md](ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)——ACP 2.0 四组合协作总纲。
- [RPH_Agent_Three_Phase_Roadmap_20261027(1).md](<RPH_Agent_Three_Phase_Roadmap_20261027(1).md>)——RPH Agent 三阶段路线（初稿 10-27，现状核查 10-29，产品讨论补充 10-30）。
  **文件名里的 `(1)` 不要改**：`reports/acp2_function_audit_20261028/evidence/baseline.json`、`rph/README.md` 等 10 处引用它，其中包含审计证据快照；改名会破坏证据可比性。该文件的旧 10-27 初稿仍在 `reference/next-change/`，见下节说明。

## 3. 文档头部约定

每个"仍在用"的文档，标题后三行内给出一致信息；这是现有做法，新文件照此写：

```markdown
# 标题

**日期**：YYYY-MM-DD（或 `版本：vX.Y · YYYY-MM-DD`）
**性质**：方案（未实施）| 实施记录（已落地，需给证据）| 契约（机器校验）| 评审（结论已并入）
**上位/取代**：上位方案链接；或被谁取代
```

三条硬规则：

1. **一份方案一个文件**，进展与切片验收追加到同一文件的新小节，不另建并行总方案。
2. **方案 ↔ 实施分开措辞**：`性质：方案` 的文件不得写成"已实现/已验收"；实施记录必须给出可复跑的命令或证据路径。
3. **不写绝对路径**（`E:\...`）与本机用户名；引用用仓库相对路径或相对链接。

## 4. 被代码钉住的文档（改路径必须同步改代码）

| 文档 | 引用方 |
|---|---|
| [ACP_Services_First_Slice_Contracts](reference/next-change/ACP_Services_First_Slice_Contracts.md) | `src/acp_services/contracts/{access,envelope,events}.py`、`data/contracts.py`、`runtime/freshness.py`（docstring） |
| [ACP_Services_Component_Manifest](reference/next-change/ACP_Services_Component_Manifest.md) | `src/acp_services/workspace/identity_lifecycle.py` |
| [ACP_Services_Identity_Support_Scope](reference/next-change/ACP_Services_Identity_Support_Scope.md) | `src/acp_services/runtime/lab_capabilities.py`、`workspace/identity_lifecycle.py` |
| [ACP_Parallel_Plan_2_Services_20261018](reference/next-change/ACP_Parallel_Plan_2_Services_20261018.md) | `tests/test_f4_scope_audit.py`（站点登记注释） |
| [ACP_CCCP_Task_API_DevDoc](reference/current/ACP_CCCP_Task_API_DevDoc.md) | `tests/test_cccp_calculation_contracts.py` |
| [ACP_CCCP_Call_Matrix](reference/current/ACP_CCCP_Call_Matrix.md) | `tests/test_acp_workflow_task_matrix.py` |
| [ACP_Unified_Execution_Target_DevDoc.txt](reference/current/ACP_Unified_Execution_Target_DevDoc.txt) | `tests/test_acp_execution_target.py` |
| [ACP_Services_Closure_20261019](reference/current/ACP_Services_Closure_20261019.md) | `tests/test_services_closure_record.py`、`test_services_doc_sync.py` |
| [ACP_Services_Four_Combination_Acceptance](reference/current/ACP_Services_Four_Combination_Acceptance.md) | `tests/test_services_four_combination_records.py` |
| [ACP_Services_Release_Prep_20261019](reference/current/ACP_Services_Release_Prep_20261019.md) | `tests/test_services_release_prep.py` |
| [ACP_Function_Test_Manual](reference/current/ACP_Function_Test_Manual.md) | `tests/test_services_doc_sync.py`（§1.5.1 内路径需存在） |
| [主方案](ACP_Architecture_Adjustment_Plan_20261016.md) | `tests/test_services_doc_sync.py`（§45 内路径需存在） |
| **必须带状态块**（6 篇，见 [next-change 索引](reference/next-change/README.md)） | `Component_Manifest` · `Diagnostics_Contract` · `Freshness_Contract` · `Identity_Support_Scope` · `MCP_Client_Matrix` · `Metering_Policy` — `tests/test_services_doc_sync.py` 要求恰好一处 `SERVICES_STATUS_SYNC:todo70` 状态块 |
| **不得带状态块**（3 篇） | `First_Slice_Contracts` · `Events_A04_Contract` · `Standalone_Recipe` — 同一测试；这两篇契约的副本摘要已固定 |

> 移动上表中的文档，等于同时改代码/测试；提交里必须两处一起改。

## 5. 新增或修改文档的流程

1. **新方案** → `reference/next-change/`，命名 `ACP_<主题>_<YYYYMMDD>.md`；在 [next-change 索引](reference/next-change/README.md) 加一行（含状态）。
2. **方案落地** → 在同一文件追加"实施记录"小节并改 `性质`，**不另开新文件**；证据写命令 + 报告路径。
3. **被取代** → 移入 `archive/<当天日期>/history/`，在该批次 `README.md` 迁移表登记（原内容 / 归档原因 / 取代者），更新 `relocations.json`，改所有指向旧位置的链接。
4. **契约冻结** → 需要机器校验的放 `reference/next-change/acp2-contracts/`，并在 `manifest.json` 登记；该目录不走第 3 步的自由迁移。
5. 收尾：`python scripts/check_docs_links.py`，再跑受影响的门（见 §4 与根 `AGENTS.md` COMMANDS）。

## 6. 归档规则

- 归档**不删除内容**，只让"当前入口"变干净；旧稿中的未结事项只有重新纳入现行方案才回到范围。
- 判定"可归档"的三个条件：**无**源码/测试/脚本引用、**已被**后续方案取代、内容仍值得追溯。
- 只被 `reports/**` 历史证据（`git status` 快照、`baseline.json` 文档摘要）提到的，不算在用——那些是留痕，**不改写**。
- 批次目录以"归档动作日期"命名，不按文档自身日期。
- **索引导航，正文冻结**：归档批次的 `README.md` 迁移表属于导航，允许把链接改到当前实际位置；归档目录里的正文、`relocations.json`、以及 `acp2-contracts/` 内部指向旧位置的历史引用都保持原样。

## 7. 不要手改的东西

- `reference/next-change/acp2-contracts/**`（schema / manifest / 摘要；`python scripts/acceptance/validate_acp2_contracts.py` 校验）
- `reference/next-change/examples/acp2_mcp_stdio_probe.py`（`scripts/acceptance/*` 引用该路径）
- `archive/**`（冻结；只允许加批注指针）
- `tests/baseline/**`（审计痕迹，不是 fixture）
- 任何 `src/`、`tests/` 里被别人 docstring 引用的路径——改名前先 `grep`

## 8. 链接自检

```bash
python scripts/check_docs_links.py            # 检查 docs/ 下的相对链接与 docs/... 路径引用
python scripts/check_docs_links.py --verbose  # 打印全部已解析条目
```

检查范围：`docs/` 下的 Markdown 相对链接和 `docs/...` 形式的仓库相对路径；跳过代码块、`archive/`、`acp2-contracts/`、带 `:行号` 的引用与外部 URL。当前基线为 **0 处失效**。
