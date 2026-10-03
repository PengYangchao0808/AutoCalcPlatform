# Migration ledger — acp→cccp architecture remediation（迁移期唯一实现当前驻点台账）

**Created:** Wave 0 / todo 1 (baseline `main@2a23b93`)
**Rules:** root `AGENTS.md` § "MIGRATION PERIOD RULES"；最终收口在 todo 32 并入 ANTI-PATTERNS #17。

## 行格式（row format）

| 列 | 含义 |
|---|---|
| 能力 (capability) | 计算能力/任务基元名（如 `run_scan`） |
| 当前实现驻点 (current implementation station) | 实现体（非 shim）当前唯一所在文件 |
| 兼容入口 (compat entry) | 迁移期保留的对外入口/重导出面 |
| 生产消费者 (production consumer) | 生产代码中调用该能力的路径 |
| 退出条件 (exit condition) | 该行可删除/收口的判据 |
| 验收测试 (acceptance test) | 守护该行状态的测试 |

规则：实现体移动即改「当前实现驻点」列；退出条件满足才删行；**新能力（含 P2 任务）先加行再落码**。每基元跨两根（`src/acp/calculations/primitives` + `src/cccp/calculation`）恰好一个实现体——`tests/test_architecture_invariants.py::test_unique_primitive_definitions` 守护。

## 台账（Wave 0 初始行）

| 能力 | 当前实现驻点 | 兼容入口 | 生产消费者 | 退出条件 | 验收测试 |
|---|---|---|---|---|---|
| `run_singlepoint` | `src/acp/calculations/primitives/singlepoint.py` | `acp.calculations.primitives.run_singlepoint` | `calculations/executor.py`、`workflows/simple.py`、`calculations/batch`、`calculations/pes` | 实现体移入 `cccp.calculation` 且 acp 侧仅剩纯 shim；ACP 路径回归绿 | `test_architecture_invariants::test_unique_primitive_definitions` |
| `run_optimize` | `src/acp/calculations/primitives/optimize.py` | `acp.calculations.primitives.run_optimize` | `executor.py`、`workflows/simple.py`、batch/pes | 同上 | 同上 |
| `run_frequency` | `src/acp/calculations/primitives/frequency.py` | `acp.calculations.primitives.run_frequency` | `executor.py`、`workflows/simple.py`、batch | 同上 | 同上 |
| `run_scan` | `src/acp/calculations/primitives/scan.py` | `acp.calculations.primitives.run_scan` | `workflows/simple.py`、`calculations/pes/scan.py` | 同上 + grep gate `unique_run_scan` 重定向到新驻点 | 同上 + `check_grep_gates.py` 冻结 pin 重定向记录 |
| `run_irc` | `src/acp/calculations/primitives/irc.py` | `acp.calculations.primitives.run_irc` | `workflows/simple.py`、irc 工作流 | 同上 + grep gate `unique_run_irc` 重定向 | 同上 |
| `ThermochemistryCalculator` | `src/acp/calculations/primitives/thermochemistry.py`（仅输入准备 + `CalculationResult` envelope 映射；执行/归一化已委托下一行共享驻点） | `acp.calculations.primitives.ThermochemistryCalculator` | `calculations/executor.py`、`calculations/batch/engine.py` | todo 22 `run_thermochemistry` 落地并调用**同一**共享适配后，由 todo 22/23 收口删除旧基元（公共 API/单次 Shermo/六参数语义不变） | `tests/test_thermochemistry.py`（双入口行为回归）+ `tests/test_batch_optimize.py` |
| Shermo 共享执行/归一化（`execute_shermo` + 标准态/单位/gibbs/metadata） | `src/cccp/qc/shermo_adapter.py` + `src/cccp/qc/thermo_normalize.py` | `acp.calculations.primitives._thermochemistry_{input,support}`（纯 re-export shim）；`ThermochemistryCalculator.compute` 与 `ExternalBackend.thermochemistry` 两入口均委托 | `acp/backends/external_backend.py`、`acp/calculations/primitives/thermochemistry.py`；todo 22 起 `cccp.calculation` 任务入口同用（不新增 runner 调用路径） | 永久驻点（无退出）；仅兼容面（shim/旧基元）随 todo 22/23 收口 | `tests/test_thermochemistry.py`（六参数 spy/单次启动/1atm·1M 基线/双入口默认输出路径）+ `tests/test_cccp_isolation.py::test_shermo_path_isolated` + grep 门 `wave2_shermo_external`/`final_shermo` |
| 批量 SP 共享核心（`acp.backends.batch.batch_single_point`） | `src/acp/backends/batch.py` | `acp.backends.batch`（隔离的遗留 backend-direct 面） | `calculations/batch/_singlepoint_execution.py` | 生产路径改走单任务核心；`legacy_batch_quarantine` 门零豁免 | todo 4 门 + batch 特征化 |
| Hessian 策略（`Recalc_Hess` 分级默认） | `src/cccp/qc/hessian_policy.py` | `acp.chem.composition`（纯 re-export shim） | `cccp/qc/interfaces/orca.py`（`_get_resolver` 缓存）、`cccp/core/protocols.py:281`、`acp/catalog.py`、`acp/api/v1_routes.py`、`calculations/batch/options.py`、`confsearch/shared/helpers.py`、`workflows/energy_shared.py`、`acp/cli.py` | ACP 消费者全部直连 cccp 后删 shim；`cccp_imports_acp` 门清零（todo 9） | `tests/test_cccp_hessian_policy.py` + `tests/test_acp_chem_composition.py` + `tests/test_cccp_isolation.py` |
| `CalculationPlanExecutor`（改线） | `src/acp/calculations/executor.py` | `acp.calculations.executor` | `workflows/simple.py`、CLI | dispatch 表改经 `cccp.calculation.run_*`，checkpoint 恢复兼容（`tests/baseline/recovery_fixtures/`） | `test_calculation_executor.py` + recovery fixtures 冒烟 |
| METHOD_META 计算语义 + 查询 API（`method_meta()`/`case_insensitive_get()`） | `src/cccp/qc/method_meta.py` | `acp.catalog.METHOD_META`/`BASIS_CATALOG`/`_case_insensitive_get`（纯 re-export）；`cccp.qc.method_meta.method_meta` | `cccp/qc/interfaces/orca.py::_resolve_method_meta`、`acp/catalog.py`、`acp/calculations/levels.py`、`acp/api`/`scheduler`（经 acp.catalog） | acp 侧消费者全部直连 cccp 后收口 re-export（todo 32）；API 载荷形状不变 | `tests/test_p0_characterization.py::TestMethodMetaSnapshot` + `tests/test_acp_catalog.py` parity + `tests/test_cccp_isolation.py` |
| 参数解析单源（`ResolvedCalculationSpec`：优先级/空值/冲突三表 + clamp 语义） | `src/cccp/qc/resolved_spec.py` | `acp.catalog._clamp_to_functional`/`_resolve_field_default`（转调 cccp 的兼容入口） | `acp/catalog.py`（clamp/默认值/UI 摘要）；todo 12/17 起缓存签名与执行输入同源消费 | 执行路径/缓存身份直取 `ResolvedCalculationSpec` 且 ACP 仅剩历史迁移与告警（todo 12/17 落地后删兼容转调） | `tests/test_cccp_resolved_spec.py`（三表/优先级/空值/冲突）+ `tests/test_acp_catalog.py` clamp 用例 + `tests/test_aux_basis_compat.py` |

P2 任务（`conformer_search`/`md_sampling`/`clustering`/`xtb_path_search`/`censo_refine`/`nmr_shielding`/`orca_gradient`）：**落码前先加行**（todo 42/43）。

## 跨版本恢复 fixtures

`tests/baseline/recovery_fixtures/`（生成脚本同目录）：checkpoint（已完成+未完成并存）、部分失败结果、历史 manifest、批量 SP 缓存、远程路径引用。迁移后 A8 验收用：`continue` 不重算已完成步骤、不丢工件、不复用不兼容缓存。
