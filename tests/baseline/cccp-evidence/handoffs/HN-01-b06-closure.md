# HN-01 — B06 逐工作流科学闭包交接（per-workflow closure handoff）

| 字段 | 值 |
|---|---|
| HN 编号 | `HN-01`（C 侧 `tests/baseline/cccp-evidence/handoff_registry.md` §2 索引行） |
| owner / provider / consumer | C 路科学负责人 / C / B（B06 独立 worker、B-T72 注入移除、B-T31/T78 独立装配） |
| tracked 位置 | `tests/baseline/cccp-evidence/handoffs/HN-01-b06-closure.md`（`.omo/` 被 gitignore，交接物必须 tracked；编号与字段口径按 B-T3 裁定的 C 侧 registry §1） |
| 计划来源 | `.omo/plans/acp-parallel-plan-3-cccp.md` todo 10（C01-s4）；交付内容=索引 §5 十要素 + 评审 C4 四状态表 + 评审 R2-P0-4 注入/装配列 |
| B 侧消费入口 | `docs/reference/next-change/ACP_Parallel_Plan_2_Services_20261018.md:88-98`（B06：按 C01 闭包逐工作流启用；未满足切片保持旧兼容路径且不声称 worker 已独立） |
| status | `delivered`（provider 侧；**未 integrated** — B 应用并验收前不得置 `integrated`） |
| applied_commit（provider 交付提交） | `ffcf1b821c89100ac0a5166a8cbc3cbe3b751a66`（HN-01 入库提交）；`handoff_registry.md` HN-01 行回填（`delivered` + 该 sha）随本交付第二提交入库 |
| 范围 | **T10a 最小交付**（8 个已判独立工作流闭包记录，立即交付，不等 T8/T9）+ **T10b 滚动汇总**（阻塞工作流与阻塞任务；后续 HN 包增量更新） |

---

## 0. 交付范围与 T8/T9 范围状态（显式记录）

- **T10a（本次交付）**：`singlepoint / optimize / frequency / scan / irc / casscf / xtb_optimize / OrcaGradient` 8 个工作流的 B06 闭包记录（§12）。
  依据：T3 三分类（`c00-closure-classification.md`：类 1 = 9 行）+ T7 simple 系逐项判定（`c00-first-slice-decision.md` §1.2：**8/10 已独立**）+ T48 隔离能力（`workflow_task_map.md` 15/15 probe PASS）。
- **T8/T9 = `NOT_APPLICABLE`（范围状态，非"完成"）**：T7 verdict B —— 本次首批范围无 T8/T9 实施面（xtb-crest 闭包符号唯一归属 T20/T21/T24；次片 NMR 归 T25–T30）。
  显式引用：`c01-first-slice-spec.md` **§7**（7.1 判定 / 7.2 机制论证 / 7.3 simple 系同等隔离证据 / 7.4 阻断者归属）；证据原文 `t7-isolation-evidence.txt`（E-1..E-4）。
  **不读作删除后续科学迁移**（全路线仍由 T20–T30 承担）；亦不以 T8/T9 未完成阻塞 T10a（计划 todo 10 Must NOT 第 5 条）。
- **T10b（滚动，未完成）**：§13 阻塞表 + §15 未决记录；随 T20–T30/T24 输出增量更新（参考关系，非本次前置）。

## 1. 要素① 基线 revision（base_revision）

- `provider_base = c1b34154296b4af24deaf1a73471b00bae9ef6c1`（本任务开工 fresh 快照，2026-10-18T21:14:28+08:00，`branch=feat/frontend-plan1-closure`）
- `consumer_applied = pending（B 未应用；无 B 侧应用提交 —— 本包全部补丁状态=已交付 delivered，零 integrated）`
- `repo_state = dirty_worktree`（`git status --short` = 16 条，全部归属 Plan-1 前端在途 / Plan-2 Services 在途 / joint 规划评审文档；**无一条属本 C 路**；本任务只写 `tests/baseline/cccp-evidence/{handoffs/HN-01-b06-closure.md, handoff_registry.md}`）
- `contract_freeze = T2 台账（c00-closure-ledger）+ T3 三分类 + T4 数值基线 + T5 写权登记 + T7 首片规格 + T48 隔离能力（2026-10-18）`
- 先前 C 路登记基线（对照）：write/handoff registry = `dfad6b30e4aa01fa3e0249ac3eb00e313263f10d`；workflow_task_map = `0394393b33f1c4900c8eb17da343f7dbdc2098bb`。

## 2. 要素② 调用方 / 旧 writer（callers & legacy writer）

> 逐工作流 `legacy_writer`（现驻 acp 的旧写者/编排）与 `new_owner`（唯一执行驻点）映射；引用清单权威源 = `tests/baseline/cccp-evidence/workflow_task_map.md`（15 行，T48 harness 每行 probe PASS）+ `c00-closure-classification.md` §2（file:line 逐行）。

| workflow | legacy writer / 调用方（file:line，含 lazy/registry 字符串） | new_owner（唯一执行驻点） |
|---|---|---|
| singlepoint | `acp/calculations/primitives/singlepoint.py:36,41`（纯转发）；`workflows/simple.py:573-594`；dispatch `executor.py:267` | `cccp/calculation/tasks/singlepoint.py:54` |
| optimize | `primitives/optimize.py:129,138`；`workflows/simple.py:622-643`；dispatch `executor.py:268` | `cccp/calculation/tasks/optimize.py:339` |
| frequency | `primitives/frequency.py:40,45`；`workflows/simple.py:670-691`；dispatch `executor.py:269`；发布投影 `acp/results/frequencies.py:11-30`（`primitives/frequency.py:131`，C03 面） | `cccp/calculation/tasks/frequency.py:62` + `frequency_parse.py:54,72` |
| scan | `primitives/scan.py:65`；`workflows/simple.py:694-714` + `prepare_scan_input:138-208`；dispatch `executor.py:270` | `cccp/calculation/tasks/scan.py:78`（`build_scan_plan:245`） |
| irc | `workflows/irc.py:14-23`（编排/发布）；`simple.py:26` re-export；validation 为 shim（`calculations/irc/validation.py:1-10` → cccp）；**无 `_PRIMITIVE_DISPATCH` 条目**（`executor.py:266-273`，静态） | `cccp/calculation/tasks/irc.py:111` + `irc_endpoints` |
| casscf | `primitives/casscf.py:39,44`；`workflows/simple.py:597-619`；dispatch `executor.py:272`；**stage-plan provider 缺**（`stage_tasks.py:700-712`，静态） | `cccp/calculation/tasks/casscf.py:201` |
| xtb_optimize | `primitives/optimize.py:138`；backend 选择 `primitives/_common.py:63`（`resources["backend"]="xtb"`）；`workflows/simple.py:646-667`；dispatch `executor.py:268` | `cccp/calculation/tasks/optimize.py:339`（复用）+ `cccp/backends/xtb.py` |
| OrcaGradient | `workflows/orca_gradient.py:553`；`legacy_adapters.py`（DATA TRANSFORM ONLY）；`acp.backends.orca` shim（`BOHR_ANGSTROM` → `cccp/backends/orca.py:26`） | `cccp/calculation/tasks/orca_gradient.py:67` |
| XtbConstrainedOpt | `workflows/xtb_constrained_opt.py:596`（`validate_request:276`）；`pes/outputs.copy_xyz_atomic`（纯 I/O）；`legacy_adapters`；**stage-plan provider 缺**（静态） | `cccp/calculation/tasks/xtb_constrained_opt.py:210` |
| XtbPathSearch | `workflows/xtb_path.py:26-29`（import `pes/path_analysis`）、`:541,:550`（发布半段算弧长/路径 profile） | `cccp/calculation/tasks/xtb_path_search.py:86`（任务核已独立；发布半段科学仍 acp → T22） |
| Confsearch | `protocols/xtb_crest.py:26,29`（lazy `run_ensemble_generation`）、`censo_crest.py:39,65`、`xtb_md.py:34-39`、`xtbmd_censo.py:31`；registry-string `confsearch/engine.py:141`、`workflows/registry.py:31-156`；PEP 562 动态 `workflows/__init__.py:41-73` | `cccp/calculation/tasks/{conformer_search:261,md_sampling:75,clustering:71,censo_refine:64}`（协议闭包科学仍 acp → T20/T21/T24） |
| PESsearch | `workflows/pes_search.py:20-29`；`pes/*` 科学（atom_mapping/path_analysis:20,285,523,576/bond_changes/candidates/validation/path_selection/atom_selection） | `cccp/calculation/tasks/{scan:78,singlepoint:54}`（PES 科学仍 acp → T22） |
| tsmode | `tsmode/engine.py:37-81`（编排+科学门控）；`workflows/tsmode.py:125-166`；科学 `mode_mapping.py:1-45`/`source.py`/`validation.py` | `cccp/calculation/tasks/{frequency:62,optimize:339}`（组合执行；映射/验证科学 → T23） |
| nmr | `workflows/nmr.py:150-152`（直连任务核 import，绕 primitives）、`:797,:842,:1629`（调用）；分析层 `acp/nmr/*` 25 模块 | `cccp/calculation/tasks/{conformer_search:261,censo_refine:64,nmr_shielding:55}`（分析层 → T25–T30） |
| BatchOptimize | `batch/engine.py:1113,1210,1244,1268`（仅派发 run_optimize/run_frequency/run_singlepoint/execute_thermochemistry，全纯转发）；`workflows/batch_optimize.py` | 上述 4 任务核（编排无算法 → 类 3 归 B 平台编排） |

## 3. 要素③ 真实签名 / schema（exact signature / schema）

- `signature`：15 任务核统一形参面 `run_<task>(request: TaskRequest, *, context: TaskContext | None = None, ...) -> TaskResult`（file:line 见 §2 new_owner 列；`TaskContext` 不序列化 —— B06-1 已冻结该约束）。ACP 侧旧入口 `acp.calculations.primitives.*::run_*` 与 `CalculationPlanExecutor` dispatch 签名**不变**（纯转发，ANTI #17）。
- `schema`：typed 契约驻 `src/cccp/calculation/{requests,results}.py`（TaskRequest/TaskResult/payload）；科学产品契约 `products/v1`（T11，`src/cccp/calculation/products/*`，`PRODUCT_SCHEMA_VERSION=1`）；任务/attempt/release 身份沿 `acp/calculations/identity.py`（纯算法已下沉 `cccp.analysis.identity`，T53）。
- `compat`：旧公开导入同函数/同类身份保持（RT-SAME/RT-CONV 见 `write_registry.md` 逐行「转发·身份·导出」列）；B06 envelope 兼容版本 v1（现状隐式）→ v2（envelope 显式），由 B06-1 定义（B 侧）。

## 4. 要素④ 版本 / 身份（version / identity）

- `contract_version` = 产品 `products/v1`（结构/轨迹，T11 已交付；曲线/振动 = T12 pending）；manifest = `result_manifest v2`（`acp/storage/manifest.py:18-96`）；`sampling_history_v1`（`confsearch/sampling_models.py:48`）。
- `identity_keys` = `plan_identity`/`step_identity` 两层（有效参数/内容摘要/依赖工件摘要，EXECUTION PROTOCOL #3）+ `content_sha256`/`identity_fingerprint`（`cccp.analysis.identity`）。
- `software_versions` = orca 6.1.1 · xtb 6.7.1 · crest 3.0.2 · censo 3.0.8 · shermo 2.6.1 · isostat(Molclus 1.14)（`c00-baseline.md §3`，经 `cccp.software.discover_all` 口径；`/usr/bin/orca` 为屏幕阅读器，非 QC ORCA）。
- `asset_versions` = NMR 模型 8 文件 65,513,350 B（`src/acp/nmr/models/`，sha256 见 `build_spec_first_batch.md §2`）——迁移/打包归 T55/T25，**本包不携带**；`dp5q_stub_params.json` 缺失（登记 gap，不补）。

## 5. 要素⑤ 错误 / 取消 / 超时（error / cancel / timeout）

- `error_types`：任务核结构化失败 `TaskResult(status="failed", error_kind="backend_failure")`（隔离探针实测，§9）；输入/配置错 `TaskInputError`（T48 science-selftest 负例）；schema 损坏 `ProductSchemaError`；缺软件 → typed unavailable（不伪装成功）。
- `typed_unavailable`：`products/v1` 的 `ProductState` 与载荷严格分离；`MissingReason` 封闭词表，缺失永不补零（T11 契约）。
- `cancel` / `timeout` / `retry`：**平台面，归 B06 envelope/controller**（B 侧 B06-1/B06-2；C 侧不声明）——本包对应字段状态 = `pending（B 定义）`；C 侧只保证任务核不产生成功回执于失败路径（EXECUTION PROTOCOL #2/#4 的任务核半段）。
- `retry`：发布失败只重试发布、不重算（EXECUTION PROTOCOL #3）——发布器在 acp 侧（`result_publication.py`，B补丁 pending）。

## 6. 要素⑥ 依赖装载（dependency loading）

- `allowed_imports` = stdlib + numpy/rdkit（+ 既有 pyproject 依赖）；`src/cccp/**` 不得 `import acp`（静态/懒加载/TYPE_CHECKING/registry 字符串/CLI 绕路）——T48 check1 `static-reverse-dependency` PASS（101 files scanned，§9）。
- `forbidden` = cccp→acp 任何形式；subprocess 旧 CLI 绕路（B06 Plan_2:94 禁止 Services 动态转调旧 `acp.cli`）。
- `runtime_deps` = **无新增**（T10a 8 工作流零新 import；节点依赖同步请求 → HN-05/T42，pending）。
- `assets` = 本包无资产（NMR 模型随 T55/HN-04）；`env` = `./cccp.yaml` executables 路径 + `PYTHONPATH` 隔离配方（见 `write_registry §3`）。

## 7. 要素⑦ fixture（fixtures）

- `fixture_paths`：`tests/baseline/cccp_calculation_goldens/**`（source_commit f1c49a14，sha256 pin）；`tests/baseline/recovery_fixtures/**`；`tests/fixtures/**`；`tests/fixtures/science_products/*.json`（T11 六态）。
- `coverage`：normal/golden + typed 错误路径；`empty/partial/corrupt` 覆盖在 products fixtures（T11）。
- `expected`：黄金值/容差逐项见 `c00-numeric-baseline.md`（42 行 pinned，**迁移期禁止变更**）。
- `repro_command`：`python3.11 scripts/verify_cccp_isolation.py`（15/15）；`python3.11 scripts/verify_cccp_slice_guard.py`（5/5）；定向 `pytest tests/test_cccp_isolation.py tests/test_cccp_task_*.py -q`（132 passed，`t7-isolation-evidence.txt` E-2）；`pytest tests/test_acp_workflows_{simple,xtb_path,orca_gradient,xtb_constrained_opt}.py -q`（138 passed/1 skipped，E-3）。

## 8. 要素⑧ 切换 / 撤旧清单（cutover / retire list）

- `cutover_steps`（B06 逐工作流启用，按 Plan_2:96）：B 侧按本包四状态表逐工作流启用 envelope 执行；C 侧无切换写权。对 ready-8：切换点 = worker 从旧 `acp.calculations.executor` dispatch 改为冻结 plan → 直调 `cccp.calculation.run_*`；平台编排（simple.py 计划构建、CLI handler、catalog 参数转换）保持旧路径直至 B-T72/B-T77 移除注入。
- `retire_list`：无 C 侧删除项（本包零产品改动）；旧实现删除条件 = 护栏 + 定向测试全绿 + B-T72 注入移除登记（B 侧）。
- `keep_shims`：`acp.calculations.primitives/*` 纯转发（D-KEEP）、`acp.backends/*`、`acp.calculations.irc.validation` re-export、`acp.chem.composition` shim —— **shim 命名空间 ≠ 旧实现**（评审 R2-P0-4 末条）。
- `frozen_clients`：旧任务回执/恢复 fixtures（`tests/baseline/recovery_fixtures/`）；scheduler markers 全集（ANTI #11）；PES2TS 冻结 payload v1（`pes2ts_*_request_v1`）。

## 9. 要素⑨ 证据（evidence）

### 9.1 本任务原始输出（fresh，provider_base=c1b3415）

```
$ cd /ACPServer/AutoCalcPlatform && /usr/bin/python3.11 scripts/verify_cccp_slice_guard.py; echo "EXIT=$?"
-- check: static-reverse-dependency: PASS --   (101 python files scanned under src/cccp)
-- check: public-api-import: PASS --           (imported 13/13 modules acp-free)
-- check: callable-task-core: PASS --          (isolation surface: 15 task cores; RESULT: PASS)
-- check: workflow-task-map: PASS --           (15 rows parsed; 15 active workflows expected)
-- check: science-selftest: PASS --            (success: ORCA optimize input generation; error: typed TaskInputError)
== summary ==
checks: 5  passed: 5  failed: 0
RESULT: PASS — slice guard checks all green.
EXIT=0

$ /usr/bin/python3.11 scripts/verify_cccp_isolation.py; echo "EXIT=$?"
::cccp-summary::{"leaked_acp_modules": [], "ok": true, "structured": 15, "task_count": 15}
  child exit code          : 0
  tasks reported           : 15/15
  leaked acp modules       : none
RESULT: PASS — all 15 cccp tasks returned structured, ACP-free results.
EXIT=0
```

（隔离探针中 14/15 任务回 `status="failed", error_kind="backend_failure"` 结构化失败 = 预期：探针不提供 QC 后端，判据是 structured + 零 acp 泄漏；`thermochemistry` 为 completed。）

### 9.2 T3/T7/T48 既有证据（本包引用，不重跑声明）

| 证据 | 路径 | 状态 |
|---|---|---|
| T3 三分类（9/5/1，15 行 file:line） | `.omo/evidence/acp-parallel-plan-3-cccp/c00-closure-classification.md` | PASS（静态） |
| T7 verdict B + simple 8/10 | `c01-first-slice-spec.md §7`；`c00-first-slice-decision.md §1.2` | PASS（静态+隔离） |
| T7 隔离原始输出 E-1..E-4 | `t7-isolation-evidence.txt`（132 passed / 138 passed+1 skipped / 28 passed） | PASS |
| T48 映射与 harness | `tests/baseline/cccp-evidence/workflow_task_map.md`、`slice_guard.md`；commit `7f11fa4` | PASS |
| T10a apply-check | §14 补丁清单（逐补丁 `git apply --check` 原文） | PASS（exit 0 ×3） |

### 9.3 tri-state / scope-state

- `tri_state`：隔离 15/15 **PASS**；slice guard 5/5 **PASS**；apply-check ×3 **PASS**；定向 pytest（T7 E-2/E-3）**PASS**；真实科学软件（ORCA/CREST/xTB/CENSO）**NOT_VERIFIED**（本任务未运行 QC）；调度 stage-plan/dispatch 缺口 runtime 后果 **NOT_VERIFIED**（静态-only）；安装发行 **NOT_VERIFIED**。
- `scope_state`：T8/T9 = **NOT_APPLICABLE**（T7 verdict B，本次首批范围）；T10a = **IN_SCOPE**（已交付）；T10b = **IN_SCOPE**（滚动，未关闭）；T11b/曲线-振动契约（T12）= IN_SCOPE 未开工（不影响本包）。
- 未测项（附方法）：① 全部 QC 真实运行（方法：`--run-slow --run-integration` + `CONFSEARCH_*_PATH` 导出）；② worker 独立装配端到端（方法：B-T78 独立子进程 Envelope 执行，B 侧）；③ wheel 无 acp 安装（方法：T40a/T54，C08 线）。

## 10. 要素⑩ 回退（rollback）

- `trigger` = B 应用后定向测试/护栏 FAIL、或数值偏离黄金基线。
- `steps` = B 侧 `git apply -R <patch>` 撤补丁（§14）；B06 逐工作流回退 = 该工作流保持旧兼容路径（Plan_2:96 允许未满足切片不切换）；C 侧本包零产品代码，无需代码回退（revert 本包提交即失去交接记录）。
- `reverify` = `pytest tests/test_architecture_invariants.py tests/test_cccp_isolation.py tests/test_grep_gates_script.py tests/test_scheduler_markers_audit.py -q` + `python3.11 scripts/verify_cccp_isolation.py` + `verify_cccp_slice_guard.py`。
- `blast_radius` = 仅 B06 切换面（B 侧）；本包未接入任何生产路径，未接入前零外部影响。

---

## 11. 四状态表（15 行；评审 C4 / 计划 todo 10；含 R2-P0-4 注入与装配列）

> 图例：✅=已成立（附证据） · ❌=未成立（附阻塞） · 状态词仅用 PASS/FAIL/NOT_VERIFIED（三态）与 delivered/integrated（补丁）。
> **「安装发行已验证」全部 ❌（NOT_VERIFIED）**：T40a/T54 未执行（C08 线）；T56 仅首批构建规格 delivered（`c39a-pyproject-first-batch.patch`，非安装验证）。
> 「平台编排待 B」列 ✅ 表示**该闭包的平台编排半段已明确交 B**（B06 逐工作流启用），❌ 表示该工作流还有 C 侧科学未交付。

| 工作流 | task 核已独立 | 科学 helper 已独立 | 平台编排待 B | 安装发行已验证 | 旧实现注入点（B-T72 移除/登记） | worker 独立装配需求（B-T31/B-T78） |
|---|---|---|---|---|---|---|
| singlepoint | ✅ `singlepoint.py:54`，probe PASS | ✅ 类 1（零 acp 执行科学） | ✅ B06 切换待 B | ❌ NOT_VERIFIED | `primitives/singlepoint.py:36,41` 纯转发；`executor.py:267 _PRIMITIVE_DISPATCH`；`step_requirements.py`（B-T72 端口） | 从冻结 envelope 构造 step requirements → 直调 `run_singlepoint`；不连 jobs DB、不取父进程注入对象 |
| optimize | ✅ `optimize.py:339`，probe PASS | ✅ 类 1 | ✅ | ❌ | `primitives/optimize.py:129,138`；`executor.py:268` | 同上（`run_optimize`） |
| frequency | ✅ `frequency.py:62` + `frequency_parse`，probe PASS | ✅（执行科学独立；发布投影 `results/frequencies.py` 属 C03 残留、非执行科学） | ✅ | ❌ | `primitives/frequency.py:40,45`；`executor.py:269`；发布半段 `results/frequencies.py:11-30`（C03/T19 面） | 直调 `run_frequency`；normal-modes 产品投影在迁移前仍走 acp 发布半段（B06 该段保持旧路径并登记） |
| scan | ✅ `scan.py:78`，probe PASS | ✅ 类 1 | ✅ | ❌ | `primitives/scan.py:65`；`executor.py:270`；计划构建 `workflows/simple.py:138-208,694-714` | 直调 `run_scan`；plan 构建在迁移期仍由旧装配提供（B-T72 登记 → B-T77 迁移） |
| irc | ✅ `irc.py:111`，probe PASS；validation 已 shim | ✅ 类 1 | ✅（**缺口**：`executor.py:266-273` 无 IRC dispatch 条目，静态-only） | ❌ | `workflows/irc.py:14-23` 编排/发布；provenance 入口；dispatch 表缺 IRC 项 | 直调 `run_irc`；B06 需补 IRC 步骤进入冻结 plan 或保留 CLI 路径（B 侧裁决，NOT_VERIFIED） |
| casscf | ✅ `casscf.py:201`，probe PASS | ✅ 类 1 | ✅（**缺口**：未列入 `_PLAN_COMPILER_WORKFLOWS`，`stage_tasks.py:700-712`，静态-only） | ❌ | `primitives/casscf.py:39,44`；`executor.py:272`；stage-plan provider 缺口 | 直调 `run_casscf`；stage-plan 投影缺失由 B 补 provider（B 侧，NOT_VERIFIED） |
| xtb_optimize | ✅ 复用 `optimize.py:339`（backend=xtb，`primitives/_common.py:63`），probe PASS | ✅ 类 1 | ✅ | ❌ | `primitives/{optimize,_common}.py`；backend 选择经 `_common` | 直调 `run_optimize`（backend 字段随冻结 plan 携带） |
| OrcaGradient | ✅ `orca_gradient.py:67`，probe PASS | ✅ 类 1（仅 shim `BOHR_ANGSTROM` + legacy_adapters 数据变换） | ✅ | ❌ | `workflows/orca_gradient.py:553`；`legacy_adapters.py`；payload 参数转换（B-T72「参数转换」类） | 从冻结 `pes2ts_orca_gradient_request_v1` 构造 TaskRequest（不依赖旧装配的转换器实例）→ 直调 `run_orca_gradient` |
| XtbConstrainedOpt | ✅ `xtb_constrained_opt.py:210`，T48 已纳入 probe 15/15 | ✅ 类 1 | ✅（**缺口**：stage-plan provider 缺，`stage_tasks.py:259` 有 schema 键但 `:700-712` 未列，静态-only） | ❌ | `workflows/xtb_constrained_opt.py:596`；`legacy_adapters.py`；stage-plan provider | 直调 `run_xtb_constrained_opt`；**T7 未将其记入 8/10 已独立集**（T3 隔离缺口已于 T48 消解，但 stage-plan 平台缺口仍在）→ 见 §13.7 |
| XtbPathSearch | ✅ `xtb_path_search.py:86`，probe PASS | ❌ 发布半段 `pes/path_analysis.py:20,285,523,576`（弧长/RMSD/导数）→ **T22** | ✅（任务核可切；工作流级 blocked） | ❌ | `workflows/xtb_path.py:26-29,541,550`（调用 path_analysis）；`legacy_adapters.py` | 直调 `run_xtb_path_search`；**路径 profile/弧长段在 T22 迁移前不得进 worker**（否则远端仍需旧 acp → 违反独立 worker） |
| Confsearch | ✅ 4 任务核全 probe PASS | ❌ 协议/选择/Boltzmann/采样/retired 六文件 → **T20/T21/T24**；协议 glue（`protocols/*`、engine、manifest）为 `B补丁` | ✅（工作流级 blocked-by-C 科学 + B 补丁） | ❌ | `protocols/xtb_crest.py:26,29` 等 lazy 委托 → retired 六文件；registry-string `engine.py:141`；`workflows/__init__.py:41-73` PEP 562 | 迁移完成后 worker 从冻结 plan 直调 4 任务核；**迁移前任何 worker 装配都会穿透旧 retired 引擎 → 不得声称该工作流 worker 独立**（B06 Plan_2:96 保持旧路径） |
| PESsearch | ✅ scan/singlepoint 核 probe PASS | ❌ `pes/*` 7 模块 → **T22** | ✅（工作流级 blocked-by-C 科学） | ❌ | `workflows/pes_search.py:20-29`；`pes/{engine,scan,review,outputs,contracts}`（B补丁面） | 迁移后直调核；人工 review/manifest 发布半段为平台面（B），科学半段待 T22 |
| tsmode | ✅ frequency+optimize 组合核 probe PASS | ❌ `tsmode/{mode_mapping,source,validation}` → **T23** | ✅（blocked-by-C 科学） | ❌ | `tsmode/engine.py:37-81`（编排+科学门控）；`workflows/tsmode.py:125-166` | 迁移后直调组合核；模式映射门控在 T23 前仍属旧实现（B-T72 登记） |
| nmr | ✅ 3 任务核 probe PASS（`nmr.py:150-152` 直连） | ❌ 分析层 25 模块 + 62.5 MiB 资产 → **T25–T30** | ✅（blocked-by-C 科学） | ❌ | `workflows/nmr.py:797,842,1629`（绕 primitives 直调）+ 分析层调用 | 迁移+资产统一入口（T55）后 worker 才可独立执行 nmr 闭环；分析层/acp `Structure` 依赖须先消失 |
| BatchOptimize | ✅ 4 任务核 probe PASS | ✅ 类 3（无自有科学；`chem/composition` 为 shim） | ✅（编排本身=平台，**归 B**：BatchOptimizeEngine 逐步编排/checkpoint/resume） | ❌ | `batch/engine.py:1113,1210,1244,1268` 派发 primitives；step requirements/recovery 经 B-T72 端口注入 | worker 执行逐 step 冻结 plan；Engine 的编排半段（items/checkpoint/resume）在 B-T77 迁移/移除前保持旧装配（过渡 controller 可，独立 worker 不可） |

## 12. T10a — ready-8 逐工作流闭包记录（十要素；status=delivered，本包）

> 每条含十要素；标「同 §N」者继承本包级字段（要素①④⑥全工作流一致；⑤cancel/timeout、⑨真实软件项为包级 NOT_VERIFIED/pending）。
> 共同 `blocked-by-B`（8 条全有）：**B06 切换本身是 B 的写权**（Plan_2:88-98）——C 侧交付闭包 ≠ worker 已切换；平台编排半段（CLI handler/计划构建/catalog 参数转换/dispatch 表）在 B-T72/B-T77 完成前保持旧路径。

### 12.1 singlepoint — delivered（T10a）
- ① 同 §1（provider_base=c1b3415；consumer_applied=pending）
- ② legacy=`primitives/singlepoint.py:36,41`、`simple.py:573-594`、`executor.py:267` → new=`tasks/singlepoint.py:54`
- ③ `run_singlepoint(request: TaskRequest, *, context: TaskContext|None=None) -> TaskResult`；schema=`cccp/calculation/{requests,results}.py`
- ④ 同 §4
- ⑤ 结构化失败 backend_failure；typed 错误 TaskInputError；cancel/timeout=平台（B06）pending
- ⑥ 同 §6（零新增依赖）
- ⑦ `test_cccp_task_singlepoint.py` + goldens；repro=§7 命令
- ⑧ cutover=B worker 直调 `run_singlepoint`（B 侧）；keep_shims=primitives 转发；frozen_clients=recovery fixtures
- ⑨ 隔离 15/15 PASS + E-2/E-3 PASS（§9）；真实 ORCA **NOT_VERIFIED**
- ⑩ 同 §10
- 状态：task 核 ✅ / helper ✅ / 平台编排 → B（blocked-by-B，B06）/ 安装 ❌；补丁记录：无专属补丁（无 C→B 产品补丁）；相关 pending 补丁见 §14.2。

### 12.2 optimize — delivered（T10a）
- ① 同 §1 ② legacy=`primitives/optimize.py:129,138`、`simple.py:622-643`、`executor.py:268` → new=`tasks/optimize.py:339`
- ③ `run_optimize(request, *, context=None) -> TaskResult` ④ 同 §4 ⑤ 同上 ⑥ 同 §6
- ⑦ `test_cccp_task_optimize.py` + goldens（如 `-180.000097` 倾角回归，c00-numeric-baseline）
- ⑧ cutover=worker 直调；keep_shims=primitives ⑨ 隔离+定向 PASS；真实 QC **NOT_VERIFIED** ⑩ 同 §10
- 状态：同 12.1（blocked-by-B=B06 切换）。

### 12.3 frequency — delivered（T10a，带 C03 残留标注）
- ① 同 §1 ② legacy=`primitives/frequency.py:40,45`、`simple.py:670-691`、`executor.py:269`、发布投影 `results/frequencies.py:11-30` → new=`tasks/frequency.py:62`+`frequency_parse.py:54,72`
- ③ `run_frequency(...) -> TaskResult`（payload 携 `FrequencyPayload.analysis`）④ 同 §4
- ⑤ 同上；⑥ 同 §6；⑦ `test_cccp_task_frequency.py`、`test_acp_frequency_modes.py`（native mode 升序/零模黄金）
- ⑧ cutover=worker 直调；**发布半段 `build_normal_modes_product` 迁移前留 acp**（C03/T19 面，B06 该段走旧路径并登记）
- ⑨ 隔离+定向 PASS；真实 QC **NOT_VERIFIED** ⑩ 同 §10
- 状态：task 核 ✅ / helper ✅（执行科学；投影残留 ≠ 执行科学，T3 细则②）/ 平台→B / 安装 ❌；**unresolved**：C03 残留（见 §15-U4）。

### 12.4 scan — delivered（T10a）
- ① 同 §1 ② legacy=`primitives/scan.py:65`、`simple.py:694-714`+`prepare_scan_input:138-208`、`executor.py:270` → new=`tasks/scan.py:78`（`build_scan_plan:245`）
- ③ `run_scan(...) -> TaskResult`；plan 构建在 acp（平台面）④ 同 §4（scan 每点身份=step identity）
- ⑤⑥ 同前 ⑦ `test_cccp_task_scan.py`；黄金含 `scan.py:105` 精确 627.5094740631（禁改）
- ⑧ cutover=worker 直调；plan 构建期留旧装配（B-T72 登记）⑨ 隔离+定向 PASS；真实 QC **NOT_VERIFIED** ⑩ 同 §10
- 状态：同 12.1（blocked-by-B=B06；另有 `--scants` 默认关闭的产品行为不变约束）。

### 12.5 irc — delivered（T10a，带 dispatch 缺口标注）
- ① 同 §1 ② legacy=`workflows/irc.py:14-23`、`simple.py:26` re-export；validation 已 shim（`irc/validation.py:1-10`→cccp）；**`executor.py:266-273` 无 IRC 条目** → new=`tasks/irc.py:111`+`irc_endpoints`
- ③ `run_irc(request, *, context=None) -> TaskResult` ④ 同 §4（端点分类身份）
- ⑤⑥ 同前 ⑦ `test_cccp_task_irc.py`、`test_acp_irc_trajectory.py`（TS 能量 -151.532087 禁改）
- ⑧ cutover=B06 需为 IRC 提供冻结 plan 入口（B 侧裁决：补 dispatch 或保留 CLI 路径）；keep_shims=validation re-export
- ⑨ 隔离+定向 PASS；多步 plan 承载 IRC 的 runtime 后果 **NOT_VERIFIED**（静态-only，T3 §3）
- ⑩ 同 §10 状态：task 核 ✅ / helper ✅ / 平台→B（含 dispatch 缺口）/ 安装 ❌；**unresolved U3**（§15）。

### 12.6 casscf — delivered（T10a，带 stage-plan 缺口标注）
- ① 同 §1 ② legacy=`primitives/casscf.py:39,44`、`simple.py:597-619`、`executor.py:272`；stage-plan provider 缺（`stage_tasks.py:700-712`）→ new=`tasks/casscf.py:201`
- ③ `run_casscf(request, *, context=None) -> TaskResult` ④ 同 §4（electronic-state 门控在 `cccp/calculation/_common.py`）
- ⑤⑥ 同前 ⑦ `test_cccp_task_casscf.py` + electronic-state 测试
- ⑧ cutover=worker 直调；stage-plan 投影由 B 补 provider ⑨ 隔离+定向 PASS；调度 runtime **NOT_VERIFIED**
- ⑩ 同 §10 状态：同 12.5（**unresolved U3**）。

### 12.7 xtb_optimize — delivered（T10a）
- ① 同 §1 ② legacy=`primitives/{optimize,_common}.py`（`_common.py:63` backend 选择）、`simple.py:646-667`、`executor.py:268` → new=`tasks/optimize.py:339` + `cccp/backends/xtb.py`
- ③ 复用 `run_optimize`（`resources["backend"]="xtb"` 随冻结 plan 携带）④ 同 §4
- ⑤⑥ 同前 ⑦ `test_cccp_task_optimize.py`（xtb backend 分支）
- ⑧ cutover=worker 直调（backend 字段来自 plan，不依赖旧装配）⑨ 隔离+定向 PASS；真实 xTB **NOT_VERIFIED** ⑩ 同 §10
- 状态：同 12.1。

### 12.8 OrcaGradient — delivered（T10a）
- ① 同 §1 ② legacy=`workflows/orca_gradient.py:553`、`legacy_adapters.py`（数据变换）、`acp.backends.orca` shim → new=`tasks/orca_gradient.py:67`
- ③ `run_orca_gradient(request, *, context=None) -> TaskResult`；payload=`pes2ts_orca_gradient_request_v1`（冻结）
- ④ 同 §4（PES2TS payload 身份冻结）⑤⑥ 同前
- ⑦ `test_cccp_task_orca_gradient.py`、`test_acp_workflows_orca_gradient.py`
- ⑧ cutover=worker 从冻结 payload 构造请求（参数转换器在 B-T72 登记「参数转换」类，B-T77 迁移）；keep_shims=legacy_adapters
- ⑨ 隔离+定向 PASS；真实 ORCA **NOT_VERIFIED** ⑩ 同 §10
- 状态：同 12.1（本地专用限制保持，Plan_2:96）。

## 13. T10b — 滚动汇总：阻塞工作流与阻塞任务（未关闭）

| # | 工作流 | ready/blocked | blocked-by（任务） | 说明（科学面 + 补丁面） |
|---|---|---|---|---|
| 13.1 | Confsearch | **blocked-by-C + blocked-by-B** | C：**T20**（采样/去重）、**T21**（Boltzmann/选择/result_helpers）、**T24**（retired 六文件活跃闭包）；B：协议 glue `B补丁`（`protocols/*`、`engine.py`、`manifest/report/profiles/contracts`、`cli.py`、`registry.py`，write_registry §4.1） | 4 任务核已独立（probe PASS）但协议委托链穿透旧实现（`xtb_crest.py:26`）→ 工作流不得记独立 |
| 13.2 | PESsearch | **blocked-by-C** | **T22**（`pes/*` 7 模块科学）；B 补丁 `pes/{engine,scan,review,outputs,contracts}`+`workflows/pes_search.py` | task 核已独立；PES 科学全在 acp |
| 13.3 | XtbPathSearch | **blocked-by-C** | **T22**（残留 `pes/path_analysis` 发布半段，`xtb_path.py:26-29,541,550`） | 「task 核已独立但工作流仍需 acp 科学」混合样本（T3 §4） |
| 13.4 | tsmode | **blocked-by-C** | **T23**（`tsmode/{mode_mapping,source,validation}`） | 组合执行核已独立 |
| 13.5 | nmr | **blocked-by-C** | **T25–T30**（分析层 25 模块 + 资产/打包同批 T55；`workflows/nmr.py` = B补丁） | 任务核直连已独立；分析层+62.5 MiB 资产未迁 |
| 13.6 | BatchOptimize | **blocked-by-B（平台编排）** | B（类 3：编排归平台；`batch/engine.py`、`workflows/batch_optimize.py` = B补丁） | 无 C 科学待迁；其独立性= B06/B-T77 平台编排迁移，非 C 路科学任务 |
| 13.7 | XtbConstrainedOpt | **blocked-by-B（平台缺口）** | B（stage-plan provider 缺，`stage_tasks.py:700-712`，静态-only NOT_VERIFIED） | T48 已纳入隔离 probe（15/15）；T7 未记入 8/10 已独立集 → 保守不入 T10a；科学面无阻塞 |

> 规则遵守：**单一工作流的 blocked 不外扩为整路 blocked**——ready-8 的 T10a 交付（§12）不因 §13 任何行而撤回；反之 §13 行不得标 done。

## 14. 补丁清单（B06 消费；delivered ≠ integrated）

### 14.1 已交付补丁（`git apply --check` 原文，provider_base=c1b3415，2026-10-18）

```
$ for p in tests/baseline/cccp-evidence/patches/t15-models-hartree-to-kcal.patch \
           tests/baseline/cccp-evidence/patches/t15-energy-graph-hartree-to-kcal.patch \
           tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch; do
    git apply --check "$p" && echo "APPLY_CHECK=PASS exit=0"; done
=== tests/baseline/cccp-evidence/patches/t15-models-hartree-to-kcal.patch
APPLY_CHECK=PASS exit=0
=== tests/baseline/cccp-evidence/patches/t15-energy-graph-hartree-to-kcal.patch
APPLY_CHECK=PASS exit=0
=== tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch
APPLY_CHECK=PASS exit=0
```

| 补丁 | provider 基线 | B06 消费点 | 状态 |
|---|---|---|---|
| `patches/t15-models-hartree-to-kcal.patch` | T15，commit `7d50eea`；本快照 apply-check PASS | `acp/core/models.py` 常量单点 → 全工作流共享 Structure 基础（B06 前 B 应用） | **delivered**（B 未应用；`consumer_applied=pending`） |
| `patches/t15-energy-graph-hartree-to-kcal.patch` | T15，commit `7d50eea`；apply-check PASS | `results/energy_graph.py` 常量单点 → 能量视图（Confsearch/PES 帧显示半段） | **delivered** |
| `patches/c39a-pyproject-first-batch.patch` | T56，commit `040db3b`；apply-check PASS | 打包 package-data（`cccp.analysis.nmr` 资产）——**B40a/C08 消费，非 B06 核心**；列此防漏 | **delivered** |

> 本任务**未创建任何产品补丁**（MUST NOT 遵守）；亦未 `git apply` 到活树（仅 `--check` 只读）。

### 14.2 仍需的补丁（pending，源=`write_registry.md`；**未生成，不伪造**）

| 工作流 | 待产补丁（registry 行 / §节） | 状态 |
|---|---|---|
| ready-8 共用 | §4.4：`primitives/{singlepoint,optimize,frequency,thermochemistry,scan,irc,casscf,_common,irc_trajectory,optimization_trajectory}.py`、`calculations/{result_publication,step_result,checkpoint,progress,executor}.py`、`workflows/{simple,irc}.py`；§4.1：`cli.py`、`workflows/registry.py`；§4.4：`catalog.py`；§4.10：`scheduler/{stage_tasks,registration,job_edit,store,events}.py`、`scheduler/remote/script_gen.py` | **pending**（未生成） |
| OrcaGradient / XtbPathSearch / XtbConstrainedOpt | §4.4：`workflows/{orca_gradient,xtb_path,xtb_constrained_opt}.py`、`calculations/legacy_adapters.py` | **pending** |
| Confsearch | §4.1 全组（4 protocols + `protocols/{_common,__init__}.py`、`engine.py`、`contracts.py`、`result_helpers.py`、`manifest.py`、`report.py`/`profiles.py`、retired 六文件、`workflows/{__init__,_helpers}.py`、`cli.py`、`registry.py`） | **pending** |
| PESsearch | §4.4：`pes/{engine,scan,scan_snapshot,review,outputs,contracts}.py`、`workflows/pes_search.py` | **pending** |
| tsmode | §4.4：`tsmode/{engine,contracts}.py`、`workflows/tsmode.py` | **pending** |
| nmr | §4.5：`workflows/nmr.py`（25 科学模块 = `C直写` 迁移，非补丁） | **pending** |
| BatchOptimize | §4.4：`batch/{engine,models,loaders,_items,_manifest,_tag,_xyz,options,profiles,effective_config,singlepoint,_singlepoint_*}.py`、`workflows/batch_optimize.py`、`core/{stage_labels,workflow,paths}.py` | **pending** |
| 常量收敛 | §4.7：`pes/path_analysis.py:20` 等**不同精度项禁改**（T34 决策输入，非补丁）；`models.py:19`/`energy_graph.py:24` 补丁已交付（§14.1） | 部分 delivered |

## 15. blocked-by-B 显式旗标与未决记录（unresolved）

**blocked-by-B（C 侧不得代记完成）：**
- **B1** B06 切换本身（逐工作流启用 envelope/worker；Plan_2:88-98）——本包仅交付其前置闭包。
- **B2** B-T72 注入登记与 B-T77 注入移除（§11 注入点列为 C 侧提供，**移除/验收归 B**；评审 R2-P0-4：C 不得把跨路「移除注入/worker 装配」标为 C 侧完成）。
- **B3** B-T31/B-T78 worker 独立装配（Services composition root 从冻结数据构造、不连 jobs DB；§11 装配列为需求描述，非已实现）。
- **B4** 全部 §14 补丁 `consumer_applied=pending`（B 应用+验收后才可置 `integrated`，由 B 在权威表 `tests/baseline/services-evidence/handoff_registry.md` 回填）。
- **B5** 平台缺口：irc 无 executor dispatch、casscf/XtbConstrainedOpt 无 stage-plan provider（静态-only；runtime **NOT_VERIFIED**）。

**unresolved 记录（沿 T7 §10 编号，滚动维护）：**

| ID | 项 | 归属 | 状态 |
|---|---|---|---|
| U1 | T7 R7：Confsearch worker 独立装配 | 跨路 B（blocked-by T24） | 未集成；C 不记完成 |
| U2 | T7 R12：`result_helpers.py` 写权=B补丁 而 T21 拥有科学迁移 | T21 以补丁方式实施；T50/T51 reconciliation | 仅登记 |
| U3 | T7 R11：stage-plan/dispatch 三缺口 | B（平台） | NOT_VERIFIED |
| U4 | frequency 发布投影 `results/frequencies.py`（C03 残留） | C：T19（写权 B补丁） | pending |
| U5 | 跨切面基础 `Structure`/`identity`（T52/T53 已下沉，旧路径转发待 B 补丁应用） | C：T52/T53 已交付实现；B 应用旧路径 | delivered（cccp 侧）/补丁 pending |
| U6 | XtbPathSearch profile/弧长在 T22 前不得进 worker | C：T22 → 后 B06 | pending |
| U7 | `installed` 状态全部 NOT_VERIFIED（T40a/T54 未执行） | C：C08 线（HN-04） | pending |

## 16. DoneClaim（T10）

- **交付**：本文件（HN-01，tracked，入库提交 `ffcf1b8`）+ `handoff_registry.md` HN-01 行回填（`status=delivered`、`applied_commit=ffcf1b8`）——共 2 文件、两次 pathspec 提交（同一提交信息），零产品代码/测试/fixture 改动，未 apply 任何补丁到活树。
- **验证命令与结果（原文见 §9.1、§14.1）**：
  - `python3.11 scripts/verify_cccp_slice_guard.py` → `checks: 5 passed: 5 failed: 0`，EXIT=0（T48 harness 5/5 PASS）
  - `python3.11 scripts/verify_cccp_isolation.py` → `15/15 structured, leaked=none`，EXIT=0
  - `git apply --check` ×3 补丁 → 全部 PASS exit=0
  - `git status --short`（开工快照）16 条，全属他路；本任务仅新增/修改 §上表 2 文件
- **未做（MUST NOT 遵守）**：未声称 B 侧已集成；未把 blocked 项标 done；未把单一工作流 blocked 外扩为整路；未创建产品补丁；未改产品代码/护栏。
- **NOT_VERIFIED**：真实 QC 运行、调度缺口 runtime、worker 独立端到端、安装发行（§9.3）。
