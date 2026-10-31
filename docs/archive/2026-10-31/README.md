# 2026-10-31 文档归档批次

本批次把 **已被后续方案取代的方案/评审稿** 移出 `reference/next-change/`，让该目录只剩"当前仍在用"的规划与契约。归档 **不删除内容**，也不表示原稿写错；任何未结事项只有重新纳入 `reference/next-change/` 的现行方案后，才重新进入实施范围。

- 上一批次：[archive/2026-10-16](../2026-10-16/README.md)（2026-10-16 重组批次）
- 本批次共 **29 个文件**，全部来自 `docs/reference/next-change/`，现位于 [`history/`](history/)
- 迁移机器可读记录：[`relocations.json`](relocations.json)（仓库相对路径）

## 本批次的判定口径

1. 有 **源码 docstring / 测试 / 脚本** 引用的文档一律不移动（例如 `ACP_Parallel_Plan_2_Services_20261018.md` 被 `tests/test_f4_scope_audit.py` 引用、`ACP_Services_First_Slice_Contracts.md` 被 `src/acp_services/contracts/*.py` 引用）。
2. 只被 `reports/**` 历史证据（`git status` 快照、`baseline.json` 文档摘要）提到的不算"在用"——那是历史留痕，不改写。
3. `reference/next-change/acp2-contracts/**` 是机器契约（manifest / 摘要校验），**不参与**文档迁移，其内部对旧路径的引用按快照原样保留。

## 迁移表

### A. 2026-10-16 一代：三层架构与前端并行开发（14 个）

现行架构为 **四层**（`cccp/qc` → `cccp/backends` → `cccp/calculation` → `acp` + 正交的 `acp_services`），以根 `AGENTS.md` ANTI #17 与 [`ACP_CCCP_Architecture_Report.md`](../../reference/current/ACP_CCCP_Architecture_Report.md) 为准；前端现行形态以 [ACP 3.0 前端计划](../../reference/next-change/ACP_3.0_Frontend_Plan_20261031.md) 与 [前端目标形态方案](../../reference/next-change/ACP_Frontend_Target_Workbench_Plan_20261030.md) 为准。

| 文件 | 原内容 | 归档原因 |
|---|---|---|
| [ACP_Three_Tier_Architecture_20261016.md](history/ACP_Three_Tier_Architecture_20261016.md) | 新版三层架构设计 | 被四层架构取代 |
| [ACP_Three_Tier_Cohesive_Modules_20261016.md](history/ACP_Three_Tier_Cohesive_Modules_20261016.md) | 三层高内聚模块设计 | 模块归属已按四层 + `acp_services` 重划 |
| [ACP_Module_Boundaries_and_Interfaces_20261016.md](history/ACP_Module_Boundaries_and_Interfaces_20261016.md) | 三层模块边界与调用规范 | 同上；现行边界见各包 `AGENTS.md` |
| [ACP_Open_Source_Reuse_Assessment_20261016.md](history/ACP_Open_Source_Reuse_Assessment_20261016.md) | 开源组件复用评估 | 选型结论已并入主方案与 [组件清单](../../reference/next-change/ACP_Services_Component_Manifest.md) |
| [ACP_Product_Strategy_and_Differentiation_20261016.md](history/ACP_Product_Strategy_and_Differentiation_20261016.md) | 产品战略与竞品差异化 | 定位已由 [ACP 3.0 架构](../../reference/next-change/ACP_3.0_Architecture_20261031.md) 重新表述 |
| [ACP_ACV_Frontend_Feasibility_20261016.md](history/ACP_ACV_Frontend_Feasibility_20261016.md) | ACV 归入前端的可行性分析 | 前端分层已重定义，ACV 不再单列 |
| [ACP_Architecture_Code_Quality_Audit_20261016.md](history/ACP_Architecture_Code_Quality_Audit_20261016.md) | 代码质量清理台账 | 清理批次已由 2.0 实施与 Services 收口记录承接 |
| [ACP_HTML_Decomposition_Antigravity_20261016.md](history/ACP_HTML_Decomposition_Antigravity_20261016.md) | 主 HTML 拆分任务书 | 拆分已落地，权限划分已失效 |
| [ACP_Frontend_UI_Antigravity_Gemini_20261016.md](history/ACP_Frontend_UI_Antigravity_Gemini_20261016.md) | 界面范围说明（Antigravity/Gemini） | 被前端目标形态方案取代 |
| [ACP_Frontend_Architecture_OpenCode_20261016.md](history/ACP_Frontend_Architecture_OpenCode_20261016.md) | OpenCode 前端架构任务书 | 被 UI-00…UI-05 设计与收口记录取代 |
| [ACP_Frontend_Integration_Handoff_OpenCode_20261016.md](history/ACP_Frontend_Integration_Handoff_OpenCode_20261016.md) | 模块交付与接线交接包 | 交接已完成 |
| [ACP_Frontend_Cleanup_Impl_20261016.md](history/ACP_Frontend_Cleanup_Impl_20261016.md) | 前端精简实施记录（第一轮） | 两轮精简已被后续 UI 重构覆盖 |
| [ACP_Frontend_Cleanup_Impl_20261017.md](history/ACP_Frontend_Cleanup_Impl_20261017.md) | 前端精简实施记录（第二轮） | 同上 |
| [ACP_Frontend_Redundancy_Audit_20261016.md](history/ACP_Frontend_Redundancy_Audit_20261016.md) | 前端冗余与维护性审计 | 候选已被后续 UI 设计与收口记录处置 |

### B. 2026-10-18 三路并行方案评审轮次（5 个）

三份正式计划（Plan 1/2/3）仍在 `reference/next-change/` 且被测试引用；本轮次归档的是它们的逐轮评审记录，结论已并入计划正文。

| 文件 | 原内容 | 归档原因 |
|---|---|---|
| [ACP_Parallel_Plans_Review_20261018.md](history/ACP_Parallel_Plans_Review_20261018.md) | 三路并行计划评审 R1 | 被 R2—R5 逐轮取代 |
| [ACP_Parallel_Plans_Review_R2_20261018.md](history/ACP_Parallel_Plans_Review_R2_20261018.md) | 第二轮评审 | 同上 |
| [ACP_Parallel_Plans_Review_R3_20261018.md](history/ACP_Parallel_Plans_Review_R3_20261018.md) | 第三轮复核 | 同上 |
| [ACP_Parallel_Plans_Review_R4_20261018.md](history/ACP_Parallel_Plans_Review_R4_20261018.md) | 第四轮复核（3 处待修正） | 问题已在 R5 核对 |
| [ACP_Parallel_Plans_Review_R5_20261018.md](history/ACP_Parallel_Plans_Review_R5_20261018.md) | 第五轮修订核对（矛盾已修正） | 终止轮次，结论已并入计划 |

### C. ACP 2.0 三轨计划评审轮次（6 个）

2.0 的发布构建与安装验收于 2026-10-23 完成（B25b 证据见 `reports/acp2_implementation/`，状态口径见根 `AGENTS.md`）。计划正文 A/B/C、索引与手册仍在 `reference/next-change/`。

| 文件 | 原内容 | 归档原因 |
|---|---|---|
| [ACP_2.0_Parallel_Plans_Review_20261021.md](history/ACP_2.0_Parallel_Plans_Review_20261021.md) | 三轨计划审阅建议 R1 | 被后续轮次取代 |
| [ACP_2.0_Parallel_Plans_Review_R2_20261021.md](history/ACP_2.0_Parallel_Plans_Review_R2_20261021.md) | 第二轮复核 | 同上 |
| [ACP_2.0_Parallel_Plans_Review_R3_20261021.md](history/ACP_2.0_Parallel_Plans_Review_R3_20261021.md) | 第三轮详细评审 | 同上 |
| [ACP_2.0_Parallel_Plans_Review_R4_20261022.md](history/ACP_2.0_Parallel_Plans_Review_R4_20261022.md) | 第四轮复评 | 同上 |
| [ACP_2.0_Parallel_Plans_Review_R5_20261022.md](history/ACP_2.0_Parallel_Plans_Review_R5_20261022.md) | 第五轮复评 | 同上 |
| [ACP_2.0_Parallel_Plans_Review_R6_20261022.md](history/ACP_2.0_Parallel_Plans_Review_R6_20261022.md) | 第六轮复评（终止轮次） | 结论已并入计划 |

### D. 架构验收整改评审轮次（4 个）

整改计划本体仍在 [`ACP_Architecture_Acceptance_Remediation_Plan_20261020.md`](../../reference/next-change/ACP_Architecture_Acceptance_Remediation_Plan_20261020.md)。

| 文件 | 原内容 | 归档原因 |
|---|---|---|
| [ACP_Architecture_Acceptance_Remediation_Review_20261020.md](history/ACP_Architecture_Acceptance_Remediation_Review_20261020.md) | 整改计划评审 R1 | 被 R2—R4 取代 |
| [ACP_Architecture_Acceptance_Remediation_Review_R2_20261020.md](history/ACP_Architecture_Acceptance_Remediation_Review_R2_20261020.md) | 复审 R2 | 同上 |
| [ACP_Architecture_Acceptance_Remediation_Review_R3_20261020.md](history/ACP_Architecture_Acceptance_Remediation_Review_R3_20261020.md) | 复审 R3 | 同上 |
| [ACP_Architecture_Acceptance_Remediation_Review_R4_20261020.md](history/ACP_Architecture_Acceptance_Remediation_Review_R4_20261020.md) | 复审 R4（终止轮次） | 结论已并入整改计划 |

## 迁移时同步做了什么

- **链接重写**：被移动文件内部的相对链接按新深度重算（`../` 增加一层）；调用方（主方案、`reference/current` 两篇、`ACP_Services_Component_Manifest.md`、根 `README.md`）指向旧位置的链接已改为 `archive/2026-10-31/history/`。
- **没有改写的引用**：`reports/**` 的 `git status` 快照与 `baseline.json` 摘要、`tests/baseline/**` 审计痕迹、`acp2-contracts/**` 契约快照、`docs/archive/2026-10-16/**` 冻结副本——它们记录的是"当时是什么样"，改写等于篡改证据。
- **例外：索引可以改链接**。上一批次 [2026-10-16/README.md](../2026-10-16/README.md) 的迁移表是导航索引，其中 7 行已指向本批次的新位置（只改链接，正文与 `relocations.json` 保持批次原状）。原则：**索引导航，正文冻结**。
- **未改动内容**：正文一字未改，仅链接与路径。

## 如何回滚

```bash
git mv docs/archive/2026-10-31/history/<file> docs/reference/next-change/<file>
```

批量回滚可用 `relocations.json` 的 `old`/`new` 两列反向执行；随后重跑 `python scripts/check_docs_links.py` 确认链接一致。
