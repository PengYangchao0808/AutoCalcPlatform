# reference/current — 现行契约与开发参考

本目录存放**仍约束实际行为**的文档：设计规范、接口契约、实施记录与发布记录。判定标准是"代码是否仍按它工作"，不是写作日期。

- 目录内文件**不逐条重新审计**；与代码冲突时以代码和根 `AGENTS.md` 为准，并顺手修正文档。
- 已被后续方案取代的批次记录不从这里删除，而是迁到 [`archive/`](../../archive/)；本索引只列仍在用的。
- 新增：`ACP_<主题>_DevDoc.md` / `ACP_<主题>_Design.md`；落地批次记录用 `ACP_<主题>_<动作>_<YYYYMMDD>.md`。

## 状态与审查（最新）

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_RPH_Status_20261028.md](ACP_RPH_Status_20261028.md) | ACP / RPH 当前实现、验证范围与剩余缺口（审查基线 `fd2087c7`） | 状态说明；与 `reports/acp2_function_audit_20261028/REPORT.md` 配套读，**不得**据此提升放行状态 |
| [ACP_Execution_Integrity_Rebaseline_Notes.md](ACP_Execution_Integrity_Rebaseline_Notes.md) | 执行完整性四不变量的重基线说明 | 说明（不变量正文在根 `AGENTS.md`） |

## 架构、任务与执行契约

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_CCCP_Architecture_Report.md](ACP_CCCP_Architecture_Report.md) | 四层架构符合性审计（qc → backends → calculation → acp + services） | 契约；`tests/test_cccp_isolation.py` 引用 |
| [ACP_CCCP_Task_API_DevDoc.md](ACP_CCCP_Task_API_DevDoc.md) | Task API 现行规格：`TaskRequest/TaskContext/TaskResult` 与字段映射表 | 契约；`tests/test_cccp_calculation_contracts.py` 校验字段表 |
| [ACP_CCCP_Call_Matrix.md](ACP_CCCP_Call_Matrix.md) | 15 个 active 工作流 → CCCP 任务核心的调用矩阵 | 契约；`tests/test_acp_workflow_task_matrix.py` 覆盖性校验 |
| [ACP_Unified_Execution_Target_DevDoc.txt](ACP_Unified_Execution_Target_DevDoc.txt) | 统一执行目标与 15 工作流收敛口径 | 契约；`tests/test_acp_execution_target.py` 引用 §16 |
| [ACP_Execution_Integrity_Rebaseline_Notes.md](ACP_Execution_Integrity_Rebaseline_Notes.md) | 执行完整性四不变量的重基线说明 | 说明（不变量正文在根 `AGENTS.md`） |
| [ACP_Task_Organization_DevDoc.md](ACP_Task_Organization_DevDoc.md) | 任务组织结构与命名/归属契约 | 契约 |

## 文件布局与存储

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Job_File_Layout_Spec.md](ACP_Job_File_Layout_Spec.md) | Zone 契约：任务目录命名、Zone A/B/C 归属、`result_summary.json` | 契约（v1.2） |
| [ACP_Project_Task_Storage_Design_v2.md](ACP_Project_Task_Storage_Design_v2.md) | 项目—任务存储与结果目录设计 v2 | 契约 |

## 科学能力与专项设计

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Simple_Workflows_DevDoc.txt](ACP_Simple_Workflows_DevDoc.txt) | simple 系列（singlepoint/optimize/frequency/optfreq/optfreqsp）开发文档 v6.0 | 契约；同文另有 [HTML 渲染版](ACP_Simple_Workflows_DevDoc.html) |
| [ACP_NMR_DP4_DevDoc.md](ACP_NMR_DP4_DevDoc.md) | NMR + DP4/DP5 立体化学归属工作流 | 契约 |
| [ACP_Electronic_State_CASSCF_Design.md](ACP_Electronic_State_CASSCF_Design.md) | 电子态、自旋极化单重态与 CASSCF/NEVPT2 | 契约 |
| [ACP_PES_Manual_Review_DevDoc.md](ACP_PES_Manual_Review_DevDoc.md) | PES 人工选点 → BatchOptimize 的帧契约 | 契约 |
| [ACP_PES_Task_Alignment.md](ACP_PES_Task_Alignment.md) | PES/路径类工作流 × 通用任务语义差异对照 | 契约（todo 28 口径） |
| [ACP_Memory_Resource_Chain.md](ACP_Memory_Resource_Chain.md) | 内存配置链路与核查记录 | 说明 |
| [ACP_RI_AuxBasis_Refactor_DevDoc.txt](ACP_RI_AuxBasis_Refactor_DevDoc.txt) | RI 加速与辅助基组配置重构 | 契约 |
| [ACP_MethodMeta_Frontend_DevDoc.txt](ACP_MethodMeta_Frontend_DevDoc.txt) | `METHOD_META` 前端数据驱动重构 | 契约 |

> `ACP_Simple_Workflows_DevDoc` 目前 **`.txt` 与 `.html` 两格式并存**（同源 v6.0）。`.html` 是 `AGENTS.md` 指向的渲染版，`.txt` 是纯文本版。修改时两侧同步；后续可择一收敛为单一来源（见 [2026-10-31 批次说明](../../archive/2026-10-31/README.md) 的遗留项）。

## 结果读取与查看器

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Structure_Sources_DevDoc.md](ACP_Structure_Sources_DevDoc.md) | 统一结构输入与"完成任务结果复用"设计 | 契约 |
| [ACP_Structure_Viewer_DevDoc.md](ACP_Structure_Viewer_DevDoc.md) | 结构查看器设计与帧投影口径 | 契约 |
| [ACP_Energy_Trajectory_Viewer_DevDoc.md](ACP_Energy_Trajectory_Viewer_DevDoc.md) | 能量/轨迹查看器：`VIEW_REGISTRY` 帧契约与采样管线 | 契约 |

## Services 收口与发布

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Services_Closure_20261019.md](ACP_Services_Closure_20261019.md) | 分层收口记录：模块验证 / 默认入口 / 真实功能验收（三态 PASS·FAIL·NOT_VERIFIED） | 收口记录；`tests/test_services_closure_record.py`、`test_services_doc_sync.py` |
| [ACP_Services_Four_Combination_Acceptance.md](ACP_Services_Four_Combination_Acceptance.md) | 四组合（F0/F1 × S0/S1）联合验收 | 验收记录；`tests/test_services_four_combination_records.py` |
| [ACP_Services_Release_Prep_20261019.md](ACP_Services_Release_Prep_20261019.md) | 发布准备：兼容窗口、schema 扩展/收缩/回退、重启流程 | 发布准备；`tests/test_services_release_prep.py` |
| [ACP_Services_Release_Record_20261019.md](ACP_Services_Release_Record_20261019.md) | 终验后的发布执行与冒烟记录 | 发布记录 |

## 前端批次记录

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Frontend_Baseline_Map_20261018.md](ACP_Frontend_Baseline_Map_20261018.md) | 前端基线地图与接管表（含 dirty-state 快照） | 基线记录 |
| [ACP_Frontend_Integration_20261016.md](ACP_Frontend_Integration_20261016.md) | 拆分与模块对接验收记录 | 实施记录 |
| [ACP_Frontend_Closure_20261017.md](ACP_Frontend_Closure_20261017.md) | §42 批次 R0—R6 首轮收口 | 实施记录 |
| [ACP_Frontend_Plan1_Acceptance_20261019.md](ACP_Frontend_Plan1_Acceptance_20261019.md) | Plan_1 验收与接管表关闭 | 验收记录 |
| [ACP_Frontend_Release_Package_20261018.md](ACP_Frontend_Release_Package_20261018.md) | 静态发布包与回退步骤 | 发布记录 |

> 前端**下一轮**的形态与实施计划在 [`../next-change/`](../next-change/README.md)（UI-00…UI-05、目标形态方案、ACP 3.0 前端计划），不在本目录。

## 测试与验收

| 文档 | 内容 | 状态 |
|---|---|---|
| [ACP_Function_Test_Manual.md](ACP_Function_Test_Manual.md) | 功能全览与真实计算任务测试手册（含 §1.5.1 Services 同步节） | 操作手册；`tests/test_services_doc_sync.py` 校验该节内路径 |
