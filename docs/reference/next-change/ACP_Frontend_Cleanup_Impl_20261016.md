# ACP 前端精简实施记录（2026-10-16）

依据《ACP_Frontend_Redundancy_Audit_20261016.md》执行的清理落地记录。本轮完成 FE-01/02/03/04(第一片)/06/07 与 FE-10 核验；未动 FE-05/08/09/11。

## 落地明细

| 项 | 改动 | 关键文件 |
|---|---|---|
| FE-01 | 删除 `molecule-view.js` 第二份逐字重复的 `submitDeleteProjectModal()`（保留 :554 首份） | `js/workbench/features/molecule-view.js` |
| FE-02 | 删除 `compat/api.js` 被覆盖的 KB 制 `formatBytes()`（保留生效的 KiB 制，:253） | `js/workbench/compat/api.js` |
| FE-03 | 孤儿生成器归档至迁移证据区 + 横幅与归档说明；交接文档两处更新 | `tests/baseline/refactor-evidence/{generate_energy_renderer.js,frontend-generator-archive.md}`；`docs/reference/next-change/ACP_Frontend_Integration_Handoff_OpenCode_20261016.md` |
| FE-04 第一片 | 删除启动即移除的旧 S2 静态弹窗：HTML 288 行（1532→1244）、bootstrap 2 处调用、scan-s2.js 死链 39 符号（1240→338 行，`createViewer` 3→0）、features.css 两段 83 行（现用 pes/scan 面板块保留）；8 处测试改等效守护 + 新增 `test_no_standalone_s2scan_modal`、`test_s2scan_geometry_helpers_live_anchors` | `ACP_Workbench_v2.html`、`js/workbench/{bootstrap.js,features/scan-s2.js}`、`css/workbench/features.css`、两测试文件 |
| FE-06 | `batchRoleStaticDefaults()` 收拢为共享工厂（每调用新对象），wizard/job_editor 双副本删除、job_editor IIFE 遮蔽死分支消除；HTML 于 job_editor 之前加载契约脚本；4 处测试 pin 等效更新（H3 13→14 脚本等） | `js/workbench/contracts/batch_role_defaults.js`（新）、`wizard.js`、`js/job_editor.js`、HTML、两测试文件 |
| FE-07 | ① transport 错误契约 additive：`err.status`/`err.body` + `opts.fetchImpl` 注入 + `timeoutMs: 0` 显式禁用；② 来源选择器 `_apiV2/_apiV1` 迁移为 transport 纯委托（`_apiV1` 补 status/body）；③ `candidateApi()` 迁移（保持无超时、legacy message 逐 case 适配、204/非 JSON 2xx→`{}` 兜底） | `js/client/transport.js`、`js/structure_source_picker.js`、`js/workbench/features/wizard.js`、`js/module_tests/{transport,wizard_candidate_api,structure_source_picker}.test.js`（+11 node 用例） |
| FE-10 | 核验两套二面角函数为**恒定 180° 语义错位**（非符号翻转；S2 用 A→B 投影、GS 用 B→A/IUPAC 扭角），不合并；黄金样例 5 例 + 统一前置条件成文 | `docs/reference/next-change/ACP_Frontend_FE10_Dihedral_Verification_20261016.md` + `.omo/evidence/frontend-cleanup-20261016/fe10_dihedral_check.js` |

## 保留（勿再当冗余删）

- FE-04 B 组报告链：`s2scanApplyJobStatus/s2scanLoadStep5Data/s2scanRenderChart/s2scanRenderFrameTable/s2scanRenderCandidates/s2scanUpdateJobStatusChip`（job-detail/nmr 活调用、apiRemote pin；其内 5 处对已删符号的文本引用位于 null-guard 不可达区，留待第二片）。
- `s2scanState` 全字段、`s2scanDist/Angle/Dihedral`、`s2scanResetState/s2scanCloseReport/s2scanEl`；i18n 全部 `s2scan.*` 键。
- `api/apiRemote/apiV2` 家族与 22 个能量图窄委托（审计暂缓清单仍有效）。

## 验证证据（2026-10-16）

- `node frontend/js/module_tests/run_all.js` → **66 passed, 0 failed**（45 基线 + 11 新增 + 归档移出无影响）
- `PYTHONPATH=src python3 -m pytest tests/test_frontend_sync.py tests/test_frontend_structure_picker.py tests/test_frontend_interaction.py tests/test_frontend_integration.py tests/test_frontend_candidate_workspace.py tests/test_acp_progress_sync.py tests/test_acp_edit_parameter_execution.py -q` → **557 passed, 28 skipped, 0 failed**
- 孤立抽查：`function submitDeleteProjectModal|function formatBytes` 全 frontend 各 1 处；`function batchRoleStaticDefaults` 仅 contracts 1 处；HTML `s2scan` 计数 0；scan-s2.js `createViewer` 0；contracts 标签(:1506) 先于 job_editor(:1507)。

## 验证中发现并修复的跨车道/既有缺口

1. FE-06 后 `test_acp_edit_parameter_execution.py` 4 个 node harness 缺 contracts 预载 → 新增 `_job_editor_source()` 辅助（harness 镜像页面加载序）。
2. 既有失败（先于本轮的 HTML 解构遗留，非本轮引入）：`progress_sync_regression.cjs` 与 picker 门控测试仍从 HTML 抽取已外移函数 → 改从真实生产源（`compat/api.js`/`features/job-detail.js`/`features/wizard.js`）抽取，断言语义不变。

## 剩余工作（下一轮）

- FE-04 第二片：B 组报告链改写（job-detail/nmr 调用方同步）、`s2scan.*` i18n 键成对清理、scan-s2 文件三分。
- FE-05 退役机理写操作按钮/空钩子；FE-08 大函数拆分（`openMethodConfig` ~2370 行）；FE-09 3D 创建点收敛到 mol3d_adapter；FE-11 CSS 归并。
- FE-10：按核验文档 §5 前置条件先定科学权威与黄金样例，再统一两套二面角实现（**不得当作等价重构**）。
