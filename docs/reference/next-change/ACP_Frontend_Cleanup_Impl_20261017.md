# ACP 前端精简实施记录 · 第二轮（2026-10-17）

承接 `ACP_Frontend_Cleanup_Impl_20261016.md`。本轮完成 FE-04 第二片、FE-05、FE-09、FE-10 固化；未动 FE-08/11（留后续轮次）。

## 落地明细

| 项 | 改动 | 关键数字 |
|---|---|---|
| **FE-04 第二片** | 删除 B 组报告链（6 函数 + `s2scanEl`）、`refreshS2Report`（nmr）及其调用（reaction-builder :2385）、job-detail 守卫、`s2scanState` 冗余字段（`jobStatus/jobId/profileData/candidatesData`；**`projectId` 因 energy.js 引用保留**）、全部 224 条 `s2scan.*` i18n 键（zh/en，`pes.*` 12 键保留）；apiRemote pin 收缩两条 | scan-s2.js 338→130 行；i18n.js 3357→3125 |
| **FE-05** | 删除退役机理写交互：三空函数（`_retiredPreviewReaction`/`persistConfirmedMechanismPlan`/`_retiredConfirmReaction`）、四个零调用死码（含 POST→410 隐患 `ensureMechanismDraftStudy`）、不可达块内按钮生成与 4 绑定、`bindMechanismPromoteButton` 定义+3 调用、`.mech-allow-zero` CSS、4 条 mech i18n 键；新增守护 `test_no_retired_mechanism_write_interactions` | reaction-builder 2819→2718；api(`/ 下限 57→56（台账注明）；后端 410 守护测试全部未动 |
| **FE-09** | 最后 3 个 raw `$3Dmol.createViewer` 点（wizard `initPreviewViewer`/reaction `initReactionViewer`/nmr `nmrEnsureViewer`）迁 `ACPVisualization.mol3d` adapter（options 逐字；nmr 缺库检查换 `isAvailable()`、dispose 换幂等 `destroy`）；features/ 下 raw 创建点归零 | 计数 pin ==5 不变（正则兼容两形态）；2 条字面量 pin 更新为 adapter 形态 |
| **FE-10 固化** | 新建 `module_tests/fe10_dihedral_golden.test.js`（9 用例 BEHAVIOR-FROZEN：5 黄金样例双侧 pin、180° 恒等式、零中轴 NaN-vs-null、**共线 S2 静默返 0** 缺陷 pin、angle/distance 一致性）；核验文档追加 §6 后端证据链 | 未改任何运行时数值 |

## FE-10 后端证据链（§6 摘要）

CLI `--coordinate` 4 原子即 dihedral、用户度数**纯透传**（constraints.py → ORCA `D i j k l = s, e, n` / xtb `dihedral: …, value`，代码未声明引擎符号约定）；后端唯一实测 `GeometryUtils.calculate_dihedral` 为 praxeolitic/IUPAC ≡ GS 约定，被 PES `actual_coordinate` 采纳；前端 scan 图轴优先 `actual_coordinate`。**结论：仓库内证据一致指向 IUPAC/GS 为平台自洽基准；S2 预填与测量/图轴恒差 180°。**

## 验证证据（2026-10-17）

- `node frontend/js/module_tests/run_all.js` → **75 passed, 0 failed**（66 + FE-10 黄金 9）
- 7 文件 pytest（sync/structure_picker/interaction/integration/candidate_workspace/progress_sync/edit_parameter_execution）→ **558 passed, 28 skipped, 0 failed**
- 抽查：`s2scanApplyJobStatus|refreshS2Report` 全 frontend 0 命中；`"s2scan.` i18n 0、`"pes.` 12 不变；scan-s2.js 保留 `s2scanCloseReport/s2scanResetState/s2scanDist/s2scanAngle/s2scanDihedral`；`updateSrPickButtons` 与 mech 只读展示链完整。

## 决策点：FE-10 二面角统一（待用户裁定）

建议方案：**将 `scan-s2.js:116` 的 `s2scanDihedral` 改为 IUPAC/GS 语义**（对齐后端实测与图轴），同步：黄金样例测试更新为新契约（红测纪律：单列变更、不称等价重构）、`s2scanAngle/Dist` 不变、消费点 wizard.js 无需改。影响：scan/PESsearch 向导选点预填的二面角起止值将变化 180°——**已有扫描任务若依赖旧预填习惯需注意**。不改后端与 geometry_store。

## 剩余工作

- FE-08：`openMethodConfig()` ~2370 行拆分（草稿/来源/方法表单/提交边界）——独立轮次。
- FE-11：CSS 归并（14 组相同声明块候选；先清已证实不可达样式，本轮已随 FE-04/05 清掉两批）。
- FE-05 尾巴：`renderReactionChangesPanel` 不可达块剩余部分（:452-552 候选列表渲染等）与手动编辑机器（:100-381 + HTML :472-480）——需先确认旧草稿进入路径后再清。
- FE-10 统一实施（见上决策点）。
