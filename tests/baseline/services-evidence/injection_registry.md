# Injection registry — 过渡装配注入登记表（B01-0 / todo 72）

**Created:** 2026-10-18，基线 HEAD `8a9f1b8fb37160a3e08d7734d3417ea5899c78c8`（工作区含 lane A 前端改动）。
**Owner:** B 路（`src/acp_services/**` 单写者）。
**Source of truth for the seam:** `src/acp_services/contracts/injection.py`（Protocol）+
`src/acp/services_bootstrap.py`（旧 acp 装配入口）+ `src/acp_services/bootstrap.py`（`ServicesPorts`）。

## 本阶段声明口径（评审 B1 / 计划 todo 72(d)）

本表登记的是**过渡装配注入**：旧 acp 装配入口（application bootstrap）创建旧实现实例，经 Services
最小 Protocol 注入。**本阶段只能声明「端口收敛」（port convergence），不能声明 Services 已独立。**
独立性证据归于实际移除旧实现后的任务（移除列 `todo 77` / `todo 78`）。本表不允许出现「later」式空泛表述：
每行的迁移任务与移除条件必须是具体 todo 号。

## 行格式（row format，5 列恒定）

| 列 | 含义 |
|---|---|
| 旧实现 (legacy implementation) | 当前唯一实现体的 `src/....py` 路径与符号（旧 acp 侧） |
| 目标模块 (target module) | 接收该能力的 Services Protocol 驻点 |
| 迁移任务 (migration task) | 实际把实现迁入 Services 的具体 todo / 子项 ID |
| 移除注入任务 (removal task) | 删除该注入、改用本包唯一实现的具体 todo ID |
| 验收 (acceptance) | 守护该行状态的测试路径或 node id |

行内不得出现 `|`（会破坏表格解析）；枚举用「或」连接，代码中的 `X-or-None` 写作 `X-or-None`。

## 注入登记表

| 旧实现 | 目标模块 | 迁移任务 | 移除注入任务 | 验收 |
|---|---|---|---|---|
| `acp.scheduler.store.JobStore`（db_path + schema 初始化入口） | `acp_services.contracts.injection.JobStorePort` | `todo 77`（schema/store owner 接管） | `todo 77` | `tests/test_services_injection_contracts.py::test_first_slice_uses_injected_ports` |
| `acp.scheduler.migrations.migrate`（版本化迁移入口） | `acp_services.contracts.injection.MigrationsPort` | `todo 77` | `todo 77` | `tests/test_services_injection_contracts.py::test_first_slice_uses_injected_ports` |
| `acp.scheduler.tasks.TaskIndex`（读 + sync 入口） | `acp_services.contracts.injection.TaskIndexPort` | `todo 19`（只读任务投影接管）；`todo 6`（tasks 归属） | `todo 77` | `tests/test_acp_scheduler_task_views.py` |
| `acp.scheduler.task_views.query_project_tasks`（只读投影查询） | `acp_services.contracts.injection.TaskViewQueryPort` | `todo 19` 至 `todo 20` | `todo 77` | `tests/test_acp_scheduler_task_views.py` |
| `acp.scheduler.manager.JobManager`（pause/unpause/cancel/continue/rerun/purge 控制意图） | `acp_services.contracts.injection.JobControlPort` | `todo 25` 至 `todo 26`（Runtime 用例接管同一执行 owner） | `todo 77` | `tests/test_acp_scheduler_processctl.py` |
| `acp.calculations.executor.CalculationPlanExecutor`（plan dispatch + resume） | `acp_services.contracts.injection.PlanExecutionPort` | `todo 27` 至 `todo 31`（worker 只执行冻结 plan 并写回执） | `todo 77` | `tests/test_calculation_executor.py` |
| `acp.calculations.step_requirements.evaluate_prerequisite`（步骤前置条件阻断） | `acp_services.contracts.injection.StepRequirementPort` | `todo 27` | `todo 77` | `tests/test_calculation_executor.py` |
| `acp.calculations.result_publication.register_result_manifest` 与 `load_scientific_result`（发布 + 恢复读） | `acp_services.contracts.injection.ResultPublicationPort` | `todo 28` | `todo 77` | `tests/test_result_publication.py` |
| `acp.catalog.normalize_and_validate_method_config` 与 `acp.cli._normalize_string_choices`（参数转换三 choke point） | `acp_services.contracts.injection.MethodConfigPort` | `todo 32` 或 `todo 42`（CLI/catalog 参数转换一次切换） | `todo 77` | `tests/test_acp_catalog.py` |
| `acp.scheduler.remote.paths`（storage_relative_path / compose_remote_dir / resolve_remote_dir） | `acp_services.contracts.injection.RemotePathsPort` | `todo 12`（StorageBinding 唯一分配权威接管） | `todo 77` | `tests/test_remote_storage_identity.py`；`tests/test_services_storage_binding.py` |
| `acp.scheduler.remote.release`（ensure_node_release / verify_existing_release / prune_releases） | `acp_services.contracts.injection.RemoteReleasePort` | `todo 32` 至 `todo 33`（整链切换接管） | `todo 77` | `tests/test_remote_code_release.py` |
| `acp.scheduler.remote.submission`（submission_id_for / submission_lsf_name / lease_ttl_seconds / submit_lease_valid） | `acp_services.contracts.injection.RemoteSubmissionPort` | `todo 30` 至 `todo 33`（ExecutionEnvelope/Receipt 与提交意图接管） | `todo 77` | `tests/test_remote_submission_protocol.py` |
| `acp.scheduler.projects.ProjectManager`（读取面） | `acp_services.contracts.injection.ProjectReadPort` | `todo 6`（原地迁入 `acp_services.workspace`，公开符号与表结构不变） | `todo 6`（迁移即移除该注入；旧路径改同实现转发） | `tests/test_acp_projects.py` |
| `acp.scheduler.projects.ProjectManager`（实现体已原地迁入；旧路径为同实现转发） | `acp_services.workspace.projects.ProjectManager` | `todo 6`（B01-1 原地接管项目/成员/分组/settings owner） | `todo 77`（迁移收口：删除过渡 `ProjectReadPort` 注入与兼容转发面） | `tests/test_acp_services_isolation.py::test_project_manager_is_single_implementation` |
| `acp.calculations.step_requirements.evaluate_prerequisite` 与 `acp.calculations.executor.CalculationPlanExecutor`（worker 分派前的步骤前置门与 plan 执行） | `acp_services.runtime.worker.FrozenWorker`（经 `StepRequirementPort` + `TaskExecutorPort`，默认 `CccpTaskExecutor`） | `todo 31`（worker 只执行冻结 plan、过前置门后写回执） | `todo 77`（任务执行注入收口至 Services 唯一实现） | `tests/test_services_worker_contract.py` |
| `acp.calculations.identity.compute_identity`（两层冻结 plan 身份复算，`identity.py:366-392`） | `acp_services.runtime.worker.PlanVerificationPort`（过渡期注入复算适配；独立复算归 todo 78） | `todo 31`（worker 校验冻结 plan 与 envelope 身份一致） | `todo 78`（自足装配入口从冻结数据复算身份，不再注入） | `tests/test_services_worker_contract.py` |
| `acp.scheduler.store.JobStore.transition`（revision/status/attempt CAS 状态推进） | `acp_services.contracts.injection.JobStateAdvancePort`（controller 资格校验后唯一推进面） | `todo 31`（controller 校验回执资格后经 CAS 推进） | `todo 77`（store owner 接管，删除过渡注入） | `tests/test_services_worker_contract.py` |

> **B06-2 worker 记（todo 31）**: 本阶段仅完成**端口收敛**——worker 侧 `src/acp_services/runtime/**`
> 只经显式 Protocol（`PlanVerificationPort` / `StepRequirementPort` / `TaskExecutorPort`）装配，
> 不 import `acp`、不触 jobs DB、不自增 attempt（AST 守护见 `tests/test_services_worker_contract.py`）。
> **worker 独立性不由本任务证明**，而由 todo 78（自足装配入口，新进程仅凭冻结数据构造依赖）证明；
> 上表三行的「旧实现」在 todo 77 / todo 78 实际迁入并移除注入前，仍属过渡装配。

## 九类覆盖核对（评审 B1）

| 类别 | 行 |
|---|---|
| job store（db_path + schema 初始化入口） | 1 |
| migrations | 2 |
| tasks / task_views（读 + sync 入口） | 3、4 |
| manager / controller（控制意图入口） | 5 |
| planning / executor / step requirements / recovery / publication | 6、7、8 |
| catalog / 参数解析 | 9 |
| remote paths | 10 |
| remote release | 11 |
| remote submission | 12 |
| （附加）过渡 workspace 读取面：ProjectManager 依赖闭包样例 | 13 |

`plan.md` todo 72 提到的 **Data writer/cache** 由 `todo 11` 至 `todo 14` 接管、移除条件归 `todo 77`；
本阶段**未**为 Data 建注入端口（Data 端口在 `todo 11` 才创建），故不在此表虚增行。参数转换的 CLI flag
choke point 与 catalog 归一化同属 `MethodConfigPort`（行 9）。

## 与既有台账的边界

- 能力驻点/消费者/退出条件的 6 列逐符号台账仍由
  `tests/baseline/services-evidence/migration_ledger.md` 维护；本表只登记**注入动作**及其迁移/移除归属。
- 交接「已交付 vs 已集成」状态仍由 `handoff_registry.md` 维护。
- 本表**只增不删**：新增注入先加行；移除注入后须在 `handoff_registry.md` 记录 applied_commit 后再删行。

## 待分类登记（not migrated this slice — no dual write）

- `acp.scheduler.molecule_groups`（`resolve_molecule_key` 等）：**待分类**。不在 B01-1（todo 6）
  迁移范围；本切片**不迁移、不建第二实现、不双写**。归属裁决（Workspace owner 或只读投影 owner）
  留待后续 todo（候选 todo 6 或 todo 19）明确后再登记为正式注入行。
- 本节为**散文记录**，不进入上表解析（上表每行仍须含具体迁移/移除 todo 号）；
  机器可校验状态见 `tests/test_services_injection_contracts.py::test_registry_rows_carry_concrete_migration_and_removal`。
