# C10-s1 — workflow → cccp task/helper mapping registry (workflow_task_map.md)

- **registry_version:** `workflow-task-map v1`
- **tracked_path:** `tests/baseline/cccp-evidence/workflow_task_map.md`（受版本控制；C 路 `C直写`）
- **created:** 2026-10-18
- **base_commit:** `02949631eb3fbae3ac48960d824ae7147707d19e`
- **plan_source:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 48（C10-s1）
- **sources:** `.omo/evidence/acp-parallel-plan-3-cccp/c00-closure-ledger.md`（T2，15 工作流）+ `c00-closure-classification.md`（T3，类 1/2/3）+ `tests/baseline/cccp-evidence/write_registry.md`（T5 写权）
- **harness:** `scripts/verify_cccp_slice_guard.py`（解析本表；每切片复跑并扩展）
- **consumers:** T8（能力门）、T16–T30（随片扩展）、`tests/test_cccp_slice_guard.py`（回归守护）

## 0. 语义与机器约定

- **`cccp task core (file:line)`**：该工作流科学结果的 cccp 唯一执行驻点（`src/cccp/calculation/tasks/*.py::def run_*`）。每行至少一个，引用格式 `path:line` 或 `path`。
- **`helper modules`**：得出科学结果仍需的 acp 侧模块（类 2 迁移源，`C直写`/`B补丁` 见 T5）；类 1 工作流无执行科学 helper 时写 `-`。仅作迁移定位，非反向依赖声明。
- **`current acp blocker (class)`**：T3 三分类（`class 1` 当前 task 已独立 / `class 2` 仍需旧 acp 科学 helper / `class 3` 纯平台编排）。
- **`T48 probe status`**：`PASS` = 本行全部 task core 均被 `scripts/verify_cccp_isolation.py` 的 acp-free 子进程探针覆盖（含 `run_*` structured 调用）；`GAP:<reason>` = 存在未被覆盖的 task core。任何 `GAP` 必须同时登记于 §2。
- 本表**不是计数门**：harness 断言的是每行 task core 引用可解析 + 每个 active 工作流有且仅有一行 + `T48 probe status` 与探针覆盖一致；不以文件数/行数相等作为唯一性证据。

## 1. 映射总表（15 行）

| workflow | cccp task core (file:line) | helper modules | current acp blocker (class) | T48 probe status |
|---|---|---|---|---|
| Confsearch | `src/cccp/calculation/tasks/conformer_search.py:261`; `src/cccp/calculation/tasks/md_sampling.py:75`; `src/cccp/calculation/tasks/clustering.py:71`; `src/cccp/calculation/tasks/censo_refine.py:64` | `src/acp/confsearch/selection.py`; `src/acp/confsearch/sampling.py`; `src/acp/confsearch/shared/boltzmann.py`; `src/acp/confsearch/protocols/xtb_crest.py`; `src/acp/confsearch/protocols/censo_crest.py`; `src/acp/confsearch/protocols/xtb_md.py`; `src/acp/confsearch/protocols/xtbmd_censo.py`; `src/acp/workflows/ensemble.py`; `src/acp/workflows/energy.py`; `src/acp/workflows/energy_shared.py`; `src/acp/workflows/ensemble_thermo.py`; `src/acp/workflows/xtbmd_md.py`; `src/acp/workflows/xtbmd_censo_energy.py` | class 2 | PASS |
| PESsearch | `src/cccp/calculation/tasks/scan.py:78`; `src/cccp/calculation/tasks/singlepoint.py:54` | `src/acp/calculations/pes/atom_mapping.py`; `src/acp/calculations/pes/bond_changes.py`; `src/acp/calculations/pes/path_analysis.py`; `src/acp/calculations/pes/path_selection.py`; `src/acp/calculations/pes/candidates.py`; `src/acp/calculations/pes/validation.py`; `src/acp/calculations/pes/atom_selection.py`; `src/acp/workflows/pes_search.py` | class 2 | PASS |
| BatchOptimize | `src/cccp/calculation/tasks/optimize.py:339`; `src/cccp/calculation/tasks/frequency.py:62`; `src/cccp/calculation/tasks/singlepoint.py:54`; `src/cccp/calculation/tasks/thermochemistry.py:51` | `src/acp/calculations/batch/engine.py`; `src/acp/calculations/batch/loaders.py`; `src/acp/calculations/batch/_items.py`; `src/acp/workflows/batch_optimize.py` | class 3 | PASS |
| XtbPathSearch | `src/cccp/calculation/tasks/xtb_path_search.py:86` | `src/acp/calculations/pes/path_analysis.py`; `src/acp/workflows/xtb_path.py`; `src/acp/calculations/legacy_adapters.py` | class 2 | PASS |
| OrcaGradient | `src/cccp/calculation/tasks/orca_gradient.py:67` | - | class 1 | PASS |
| XtbConstrainedOpt | `src/cccp/calculation/tasks/xtb_constrained_opt.py:210` | - | class 1 | PASS |
| irc | `src/cccp/calculation/tasks/irc.py:111` | `src/acp/calculations/irc/validation.py`; `src/acp/calculations/irc/source.py` | class 1 | PASS |
| scan | `src/cccp/calculation/tasks/scan.py:78` | - | class 1 | PASS |
| tsmode | `src/cccp/calculation/tasks/frequency.py:62`; `src/cccp/calculation/tasks/optimize.py:339` | `src/acp/calculations/tsmode/mode_mapping.py`; `src/acp/calculations/tsmode/source.py`; `src/acp/calculations/tsmode/validation.py`; `src/acp/calculations/tsmode/engine.py`; `src/acp/workflows/tsmode.py` | class 2 | PASS |
| casscf | `src/cccp/calculation/tasks/casscf.py:201` | - | class 1 | PASS |
| nmr | `src/cccp/calculation/tasks/conformer_search.py:261`; `src/cccp/calculation/tasks/censo_refine.py:64`; `src/cccp/calculation/tasks/nmr_shielding.py:55` | `src/acp/nmr/probability.py`; `src/acp/nmr/error_model.py`; `src/acp/nmr/averaging.py`; `src/acp/nmr/scaling.py`; `src/acp/nmr/assignment.py`; `src/acp/nmr/equivalence.py`; `src/acp/nmr/fchl.py`; `src/acp/nmr/spectra.py`; `src/acp/nmr/models.py`; `src/acp/workflows/nmr.py` | class 2 | PASS |
| singlepoint | `src/cccp/calculation/tasks/singlepoint.py:54` | - | class 1 | PASS |
| optimize | `src/cccp/calculation/tasks/optimize.py:339` | - | class 1 | PASS |
| frequency | `src/cccp/calculation/tasks/frequency.py:62` | `src/acp/results/frequencies.py`; `src/acp/results/orca_parser.py` | class 1 | PASS |
| xtb_optimize | `src/cccp/calculation/tasks/optimize.py:339` | - | class 1 | PASS |

## 2. 已登记缺口（registered gaps）

> 任何 `GAP:<reason>` 行必须在此逐条登记。空 = 当前无缺口。

（无 — T48 已把 `xtb_constrained_opt` 纳入 `scripts/verify_cccp_isolation.py` 探针集：任务核返回 structured、ACP-free `TaskResult`，故 T3 登记的 `EXPECTED_TASKS` 缺口在本片关闭。）

## 3. 复算命令

```bash
# 探针覆盖（15/15）
python3.11 scripts/verify_cccp_isolation.py
# 映射表完整性 + 反向依赖扫描 + 导入探针 + 注入负例
python3.11 scripts/verify_cccp_slice_guard.py
python3.11 scripts/verify_cccp_slice_guard.py --injection-selftest
```

## 4. 未验证项（NOT_VERIFIED）

| 项 | 状态 | 原因 |
|---|---|---|
| 15 工作流任一真实科学软件运行 | `NOT_VERIFIED` | T48 为静态/隔离能力，不跑真实 QC |
| class 2/3 工作流的科学 helper 迁移完成度 | `NOT_VERIFIED` | 迁移归 T16–T30；本表仅登记当前阻塞类 |
