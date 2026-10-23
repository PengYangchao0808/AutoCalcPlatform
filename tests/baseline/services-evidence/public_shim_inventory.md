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

## 6. 本轮整改新增/变更的公开面（T24 至 T33，登记不授权删除）

| 面 | 实现驻点 | 兼容/调用面 | 状态 | 守护测试 |
|---|---|---|---|---|
| 公网同源配置（2026-10-22） | `src/acp_services/workspace/boundary.py:OriginPolicy.public_origin` | `src/acp/api/server.py` 优先读取 `ACP_PUBLIC_ORIGIN`，否则读取 `acp init` 写入的 `deployment.params.public_origin`；原 OriginPolicy 默认语义保留 | `[CLOSED]`（模块回归范围） | `tests/test_services_local_boundary.py`；`tests/test_services_init_unified.py`；`tests/test_acp_drafts.py` |
| 冻结 attempt 生产器与执行权 | `src/acp_services/runtime/attempt_freeze.py`（AttemptFreezeSpec、write_frozen_attempt、AttemptLockExecutionRight） | ACP 侧 `acp.calculations.attempt_freeze` builder；`src/acp/scheduler/{runner,remote/runner}.py`；`worker_bootstrap` | `[CLOSED]` | `tests/test_services_chain_switch.py`；`tests/test_services_worker_bootstrap.py` |
| attempt 完成服务 | `src/acp_services/runtime/attempt_completion.py`（AttemptSnapshot、AttemptCompletionService） | `src/acp/services_completion_bootstrap.py:JobStoreAdvancePort`；`src/acp/scheduler/manager.py` | `[CLOSED]` | `tests/test_services_worker_contract.py`；`tests/test_services_chain_switch.py` |
| receipt 身份契约 | `src/acp_services/runtime/receipt_identity.py`（ReceiptIdentity、compare_receipt_identity） | `worker.py` 与 `receipts.py` 共用；`worker_bootstrap.py` 冲突路径 | `[CLOSED]` | `tests/test_services_worker_contract.py`；`tests/test_services_worker_bootstrap.py` |
| D01 任务完成事实与 task_result | `src/acp_services/runtime/worker.py`（decide_task_result、write_task_result、read_task_result） | `src/acp/calculations/executor.py`；`src/acp_services/runtime/step_requirements.py`；`attempt_publication.py` | `[CLOSED]` | `tests/test_services_worker_contract.py` |
| prepare 内容身份与 epoch | `src/acp_services/data/operations.py`（content_identity、client_idempotency_key、dedup_epoch、find_by_identity） | 既有 `PrepareOperationService` API 加性扩展 | `[CLOSED]` | `tests/test_services_prepare_operations.py` |
| 读取根授权快照 | `src/acp_services/data/read_root.py`（ReadRoot.attempt、CacheIndexFacts.snapshot、CacheIndexPort.entry） | `build_read_only_reader` 与 `build_cache_only_resource_reader` 加性参数；`src/acp/api/v1_routes.py` | `[CLOSED]` | `tests/test_services_read_root.py`；`tests/test_services_cache_only_switch.py` |
| 传输发布边界身份 | `src/acp_services/data/transfer.py`（identity_check、强制 authorization） | `build_transfer_pipeline` 与 `build_transfer_executor` 加性参数 | `[CLOSED]` | `tests/test_services_transfer_verify.py` |
| 默认链发布适配器（ACP 侧） | `src/acp/calculations/attempt_publication.py`（AttemptResultPublication） | 复用 `publish_result`；`src/acp/scheduler/manager.py` | `[CLOSED]` | `tests/test_services_chain_switch.py`；`tests/test_acp_workflows_simple.py` |
| singlepoint 默认 envelope 集合 | `src/acp_services/runtime/execution_chain.py`（DEFAULT_ENVELOPE_WORKFLOWS） | 既有 legacy 路径保留；DEFAULT 与 ENABLED 分离 | `[CLOSED]` | `tests/test_services_workflow_enablement.py` |
| 远端冻结链 | `src/acp/scheduler/remote/runner.py`（_upload_frozen_envelope、receipt 与 task_result fetch） | `src/acp/scheduler/remote/sync.py`（sync 含 src/acp_services） | `[CLOSED]`（真实 LSF 为 `NOT_VERIFIED`） | `tests/test_services_chain_switch.py`；`tests/test_remote_phase1.py` |
| `Path_safe` 公共命名兼容 | `src/cccp/analysis/structure/parsers.py:path_safe` | `Path_safe` 兼容别名；`src/acp/intake/parsers.py` 旧导入面保留 | `[RETAINED]` | `tests/test_acp_intake.py`；T33 import smoke |
| 本地异常死亡停止核验（2026-10-22） | `runtime/attempt_recovery.py:CgroupAttemptRecovery`；`runtime/cgroup.py` 纯资源计划 | `worker_bootstrap --execution-cgroup-root` / `ACP_EXECUTION_CGROUP_ROOT`；原 `runtime.isolation.CgroupV2Plan` / `EnvelopeProcessGroup` 保留同对象重导出 | `[OPEN]`：受控验证通过，真实委派环境 NOT_VERIFIED；旧无约束 claim 继续拒绝 | `tests/test_services_cgroup_attempt_recovery.py`；`tests/test_services_isolation.py` |

**兼容面结论：** 上述公开面均为加性或新驻点；旧 identity forwarder（`acp.scheduler.{store,migrations}`、
`acp.calculations.{contracts,step_result,step_requirements}`、`acp.catalog.method_levels_to_cli_flags`）
保持 `is` 同一对象，未新增第二实现。

## 7. B25b installed-wheel compatibility check (2026-10-23)

The clean role-wheel install in `reports/acp2_implementation/bb45a46/B/B25/` re-verified the
public shim surface **from installed site-packages**, not from this checkout:

| surface | installed check | status |
|---|---|---|
| `acp.calculations.primitives` | imports from `…/site-packages/acp/…`; tasks execute from `cccp.calculation` | `[RETAINED]` |
| `acp.backends` | pure re-export shim imports; implementation remains `cccp.backends` | `[RETAINED]` |
| `acp.compat.legacy.manifests` | read-only reader imports from the installed `acp` package | `[RETAINED]` |
| `acp.scheduler.store.JobStore is acp_services.store.store.JobStore` | identity `True` in the installed web venv | `[RETAINED]` |
| `acp.calculations.contracts.CalculationPlan is acp_services.runtime.plan_contracts.CalculationPlan` | identity `True` in the installed web venv | `[RETAINED]` |
| `acp` console command | `acp --help` works in the installed web and root role venvs | `[RETAINED]` |

Evidence: `b25.compat_imports` and `b25.standalone_cli` in the package-install receipt. No shim was
deleted or re-implemented; the root `acp` distribution and the three role distributions remain
mutually exclusive installation choices.
