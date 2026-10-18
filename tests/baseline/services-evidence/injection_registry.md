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
| `acp.scheduler.remote.paths`（storage_relative_path / compose_remote_dir / resolve_remote_dir） | `acp_services.contracts.injection.RemotePathsPort` | `todo 12`（StorageBinding 唯一分配权威接管） | `todo 77` | `tests/test_remote_storage_identity.py` |
| `acp.scheduler.remote.release`（ensure_node_release / verify_existing_release / prune_releases） | `acp_services.contracts.injection.RemoteReleasePort` | `todo 32` 至 `todo 33`（整链切换接管） | `todo 77` | `tests/test_remote_code_release.py` |
| `acp.scheduler.remote.submission`（submission_id_for / submission_lsf_name / lease_ttl_seconds / submit_lease_valid） | `acp_services.contracts.injection.RemoteSubmissionPort` | `todo 30` 至 `todo 33`（ExecutionEnvelope/Receipt 与提交意图接管） | `todo 77` | `tests/test_remote_submission_protocol.py` |
| `acp.scheduler.projects.ProjectManager`（读取面） | `acp_services.contracts.injection.ProjectReadPort` | `todo 6`（原地迁入 `acp_services.workspace`，公开符号与表结构不变） | `todo 6`（迁移即移除该注入；旧路径改同实现转发） | `tests/test_acp_projects.py` |

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
