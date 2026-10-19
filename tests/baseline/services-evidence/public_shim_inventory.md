# Public shim / historical reader / vendored asset inventory（B13-5 / todo 70）

**Created:** 2026-10-19 · **Scope:** ACP Services 收口时的公开兼容面与调用者审计。
**Rule:** 本文件只登记，不授权删除。所有 `[RETAINED]` 行在 Services 计划范围内保持可用；
删除必须另立切片、先完成调用者迁移并更新 `migration_ledger.md` 与 `tests/baseline/recovery_fixtures/` 证明。
**Companion:** `migration_ledger.md`（逐符号 6 列台账）、`docs/reference/current/ACP_Services_Closure_20261019.md`（三态收口）。

<!-- SERVICES_SHIM_INVENTORY:todo70 -->

## 1. 公开导入 shim（import path 不变，实现单根）

| 公开面 (shim) | 唯一实现根 | 审计到的生产调用者 | 状态 | 退出/保留理由 |
|---|---|---|---|---|
| `acp.calculations.primitives` | `src/cccp/calculation/tasks/` + ACP 发布契约 | `src/acp/calculations/executor.py:56-61`；`src/acp/calculations/batch/engine.py:62-65`；`src/acp/calculations/tsmode/engine.py:44-45`；`src/acp/workflows/simple.py:26-28`；`src/acp/workflows/irc.py:19`；`src/acp/cli.py:3939` | `[RETAINED]` | ANTI #17 唯一执行驻点；shim 是公共 import 兼容面，非第二实现。`tests/test_architecture_invariants.py::test_unique_primitive_definitions` 守护 |
| `acp.backends` | `src/cccp/backends/` | `src/acp/api/routes.py:207`；`src/acp/workflows/{orca_gradient,energy,energy_shared,ensemble,xtbmd_md}.py`；`src/acp/calculations/batch/{singlepoint,_singlepoint_execution,_singlepoint_models}.py`；`tests/conftest.py:16` | `[RETAINED]` | 纯 re-export shim；`backends` 层禁导任务模块，`src/cccp/**` 禁导 `acp` |
| `acp.scheduler.store` | `src/acp_services/store/store.py` | `src/acp/scheduler/manager.py:78`；`src/acp/scheduler/{registration,tasks,local_cleanup,artifacts,stage_tasks,structure_source_store,structure_source_indexer}.py`；`src/acp/scheduler/remote/{cleanup,release}.py`；`src/acp/api/v1_routes.py:253`；`src/acp/cli.py:1536` | `[RETAINED]` | identity forwarder：`JobStore`/`JobStateConflictError` 与 Services 为**同一对象**；`tests/test_services_migration_equivalence.py` 守护 |
| `acp.scheduler.migrations` | `src/acp_services/store/migrations.py` | `src/acp/scheduler/{artifacts,stage_tasks,tasks,structure_source_store}.py` | `[RETAINED]` | identity forwarder：`migrate` 同一对象；禁止在旧模块重加实现 |
| `acp.calculations.contracts` | `src/acp_services/runtime/plan_contracts.py` | `src/acp/calculations/{executor,batch/engine,primitives/*,irc/contracts}.py`；`src/acp/catalog.py`；`src/acp/scheduler/manager.py`（经旧路径） | `[RETAINED]` | identity re-export；`CalculationPlan` 等为同一对象（todo 77 闭包） |
| `acp.calculations.step_result` | `src/acp_services/runtime/step_result.py` | `src/acp/calculations/batch/engine.py:67`；`src/acp/calculations/tsmode/engine.py:46`；`src/acp/calculations/executor.py:78`；`src/acp/scheduler/manager.py:2747` | `[RETAINED]` | identity re-export；恢复语义由 `tests/test_checkpoint_resume_semantics.py` 守护 |
| `acp.calculations.step_requirements` | `src/acp_services/runtime/step_requirements.py` | `src/acp/calculations/executor.py:70`；`acp_services/runtime/worker.py`（Services-native 默认门） | `[RETAINED]` | identity forwarder：`evaluate_prerequisite` 同一对象 |
| `acp.catalog.method_levels_to_cli_flags` | `src/acp_services/runtime/method_flags.py` | `src/acp/scheduler/remote/script_gen.py:49`；`src/acp/scheduler/runner.py:1467`；`src/acp/api/v1_routes.py:2265` | `[RETAINED]` | identity forwarder；参数转换大小写折叠三 choke point 不变 |
| `acp.services_{,data_,events_,publication_}bootstrap` | `src/acp_services/**` 的过渡装配 | `src/acp/scheduler/manager.py:447,4805`；`src/acp/calculations/executor.py`；`src/acp/scheduler/remote/runner.py`；`src/acp/api/v1_routes.py:274` | `[RETAINED]` | 过渡注入面（端口收敛）；`worker_bootstrap` 已是独立组合根，不依赖这些模块 |

## 2. 历史只读 reader / 布局探针（`acp.compat.legacy`，只读保留）

`acp.compat.legacy` 下的 reader/布局探针只读保留；新代码消费数据走 `results/manifest` v2（ANTI #18）。

| 面 | 文件 | 生产调用者 | 状态 | 说明 |
|---|---|---|---|---|
| 历史 manifest reader | `src/acp/compat/legacy/manifests.py` | `src/acp/api/v1_routes.py:2658,2705`；`src/acp/results/structure_viewer.py:1519`；`src/acp/workflows/pes_search.py:79`；`src/acp/cli.py:1391`；`tests/test_recovery_fixtures_smoke.py:55` | `[RETAINED]` | 只读；ANTI #18 禁止写入。新代码走 `acp.results.manifest` v2 |
| 历史布局双探针 | `src/acp/compat/legacy/layouts.py` | `src/acp/api/v1_routes.py:926,4665,4928`；`tests/test_legacy_layout_compat.py:101,139,170` | `[RETAINED]` | 只读；只服务历史任务布局 |
| 历史 batch loader | `src/acp/compat/legacy/batch_loaders.py` | `tests/test_batch_optimize.py:79`（历史 manifest 读取） | `[RETAINED]` | 只读；不作为新任务输入权威 |

## 3. 退役工作流内部引擎 shim

| 面 | 文件 | 调用者 | 状态 | 说明 |
|---|---|---|---|---|
| retired engine modules | `src/acp/workflows/{ensemble,energy,energy_shared,ensemble_thermo,xtbmd_censo_energy,xtbmd_md}.py` | `src/acp/confsearch/protocols/`（协议引擎复用）；`src/acp/workflows/registry.py`（退役子命令拦截） | `[RETAINED]` | C01/C06 科学算法提取落 `cccp/calculation/tasks/` 后才可删除；本计划不删 |

## 4. 资产 / fixture / notice 保留（未改动）

| 资产 | 位置 | 保留理由 |
|---|---|---|
| 跨版本恢复 fixture | `tests/baseline/recovery_fixtures/` | live consumer `tests/test_recovery_fixtures_smoke.py`、`tests/test_recovery_cross_version.py`；本任务未修改、未删除 |
| DP5 模型资产 + notice | `src/cccp/analysis/nmr/model_assets/`（`atomic_reps.gz`、`frag_reps.gz`、`*.p`、`tms_references.txt`、`LICENSE-DP5`、`NOTICE.md`） | `pyproject.toml` package-data `"cccp.analysis.nmr" = ["model_assets/*"]`；随包发行，不复制第二份 |
| vendored 3Dmol | `frontend/js/vendor/3Dmol-2.5.5.min.js` + `3Dmol-LICENSE` + `README.md` | 前端渲染资产；license/notice 随包，不退化为 CDN 依赖 |

## 5. 调用者审计方法（可复跑）

```bash
# import shim callers（生产代码）
grep -rnE 'from acp\.(calculations\.primitives|backends|calculations\.(contracts|step_result|step_requirements))|from acp\.scheduler\.(store|migrations)|acp\.compat\.legacy' \
  src/acp src/acp_services --include='*.py'

# identity（同一对象，不是复制）
python3.11 - <<'PY'
import importlib
print(importlib.import_module('acp.scheduler.store').JobStore
      is importlib.import_module('acp_services.store.store').JobStore)
print(importlib.import_module('acp.calculations.contracts').CalculationPlan
      is importlib.import_module('acp_services.runtime.plan_contracts').CalculationPlan)
print(importlib.import_module('acp.calculations.step_requirements').evaluate_prerequisite
      is importlib.import_module('acp_services.runtime.step_requirements').evaluate_prerequisite)
PY
```

**审计结论：** 上表所有 `[RETAINED]` 面均有真实调用者或按设计作为历史读取/资产保留；
本任务**未删除**任何 shim、历史 reader、模型/资产/notice 或 recovery fixture。
