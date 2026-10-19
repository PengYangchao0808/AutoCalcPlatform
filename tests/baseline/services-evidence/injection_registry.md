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
| `acp.scheduler.tasks.TaskIndex`（读 + sync 入口） | `acp_services.contracts.injection.TaskIndexPort` | `todo 19`（只读任务投影接管）；`todo 6`（tasks 归属） | `todo 77` | `tests/test_acp_scheduler_task_views.py` |
| `acp.scheduler.task_views.query_project_tasks`（只读投影查询） | `acp_services.contracts.injection.TaskViewQueryPort` | `todo 19` 至 `todo 20` | `todo 77` | `tests/test_acp_scheduler_task_views.py` |
| `acp.scheduler.manager.JobManager`（pause/unpause/cancel/continue/rerun/purge 控制意图） | `acp_services.contracts.injection.JobControlPort` | `todo 25` 至 `todo 26`（Runtime 用例接管同一执行 owner） | `todo 77`（DEFERRED 残余，见「DEFERRED 注入登记表」） | `tests/test_acp_scheduler_processctl.py`；`tests/test_services_runtime_usecases.py` |
| `acp.scheduler.manager.JobManager.submit`（新任务提交入口） | `acp_services.contracts.injection.JobSubmissionPort`（Runtime `submit` 用例转发面） | `todo 25` 至 `todo 26`（Runtime 用例接管同一执行 owner） | `todo 77`（DEFERRED 残余，见「DEFERRED 注入登记表」） | `tests/test_services_runtime_usecases.py` |
| `acp.scheduler.manager.JobManager.edit_recalculate`（编辑重算入口） | `acp_services.contracts.injection.JobEditPort`（Runtime `edit_recalculate` 用例转发面） | `todo 25` 至 `todo 26`（Runtime 用例接管同一执行 owner） | `todo 77`（DEFERRED 残余，见「DEFERRED 注入登记表」） | `tests/test_services_runtime_usecases.py` |
| `acp.scheduler.registration.register_completed_cli_job`（CLI 完成作业登记） | `acp_services.contracts.injection.JobRegistrationPort`（Runtime `register_completed_job` 用例转发面） | `todo 25`（CLI 提交入口经 Runtime 用例） | `todo 77`（DEFERRED 残余，见「DEFERRED 注入登记表」） | `tests/test_services_runtime_usecases.py` |
| `acp.calculations.executor.CalculationPlanExecutor`（plan dispatch + resume） | `acp_services.contracts.injection.PlanExecutionPort` | `todo 27` 至 `todo 31`（worker 只执行冻结 plan 并写回执） | `todo 77`（DEFERRED 残余，见「DEFERRED 注入登记表」） | `tests/test_calculation_executor.py` |
| `acp.calculations.result_publication.register_result_manifest` 与 `load_scientific_result`（发布 + 恢复读） | `acp_services.contracts.injection.ResultPublicationPort` | `todo 28` | `todo 77` | `tests/test_result_publication.py` |
| `acp.catalog.normalize_and_validate_method_config` 与 `acp.cli._normalize_string_choices`（参数转换三 choke point） | `acp_services.contracts.injection.MethodConfigPort` | `todo 32` 或 `todo 42`（CLI/catalog 参数转换一次切换） | `todo 77`（DEFERRED 残余，见「DEFERRED 注入登记表」） | `tests/test_acp_catalog.py` |
| `acp.scheduler.remote.paths`（storage_relative_path / compose_remote_dir / resolve_remote_dir） | `acp_services.contracts.injection.RemotePathsPort` | `todo 12`（StorageBinding 唯一分配权威接管） | `todo 77` | `tests/test_remote_storage_identity.py`；`tests/test_services_storage_binding.py` |
| `acp.scheduler.remote.release`（ensure_node_release / verify_existing_release / prune_releases） | `acp_services.contracts.injection.RemoteReleasePort` | `todo 32` 至 `todo 33`（整链切换接管） | `todo 77` | `tests/test_remote_code_release.py` |
| `acp.scheduler.remote.submission`（submission_id_for / submission_lsf_name / lease_ttl_seconds / submit_lease_valid） | `acp_services.contracts.injection.RemoteSubmissionPort` | `todo 30` 至 `todo 33`（ExecutionEnvelope/Receipt 与提交意图接管） | `todo 77` | `tests/test_remote_submission_protocol.py` |
| `acp.scheduler.projects.ProjectManager`（读取面） | `acp_services.contracts.injection.ProjectReadPort` | `todo 6`（原地迁入 `acp_services.workspace`，公开符号与表结构不变） | `todo 6`（迁移即移除该注入；旧路径改同实现转发） | `tests/test_acp_projects.py` |
| `acp.scheduler.projects.ProjectManager`（实现体已原地迁入；旧路径为同实现转发） | `acp_services.workspace.projects.ProjectManager` | `todo 6`（B01-1 原地接管项目/成员/分组/settings owner） | `todo 77`（迁移收口：删除过渡 `ProjectReadPort` 注入与兼容转发面） | `tests/test_acp_services_isolation.py::test_project_manager_is_single_implementation` |
| `acp.calculations.step_requirements.evaluate_prerequisite` 与 `acp.calculations.executor.CalculationPlanExecutor`（worker 分派前的步骤前置门与 plan 执行） | `acp_services.runtime.worker.FrozenWorker`（步骤前置门已为 Services-native 默认；`TaskExecutorPort` 仍注入，默认 `CccpTaskExecutor`） | `todo 31`（worker 只执行冻结 plan、过前置门后写回执） | `todo 77`（步骤前置门注入已随之关闭；执行器注入待后续批次） | `tests/test_services_worker_contract.py` |
| `acp.calculations.identity.compute_identity`（两层冻结 plan 身份复算，`identity.py:366-392`） | `acp_services.runtime.worker.PlanVerificationPort`（过渡期注入复算适配；独立复算归 todo 78） | `todo 31`（worker 校验冻结 plan 与 envelope 身份一致） | `todo 78`（自足装配入口从冻结数据复算身份，不再注入） | `tests/test_services_worker_contract.py` |
| `acp.scheduler.store.JobStore.transition`（revision/status/attempt CAS 状态推进） | `acp_services.contracts.injection.JobStateAdvancePort`（controller 资格校验后唯一推进面） | `todo 31`（controller 校验回执资格后经 CAS 推进） | `todo 77`（DEFERRED 残余，见「DEFERRED 注入登记表」） | `tests/test_services_worker_contract.py` |

## 已关闭注入（B05-7 / todo 77：实现迁入 Services 并移除注入）

以下注入已由 todo 77 关闭：实现体原样迁入 `src/acp_services/`（旧 `acp` 路径保留为同实现转发，
identity `acp.X is acp_services.Y` 由 `tests/test_services_migration_equivalence.py` 守护），
`ServicesPorts` 与 `src/acp/services_bootstrap.py` 不再构造/注入对应端口。
`applied_commit` 记于 `handoff_registry.md`（B05-7 行）。

| 旧实现 | 目标模块 | 迁移任务 | 移除注入任务 | 验收 |
|---|---|---|---|---|
| `acp.scheduler.store.JobStore`（db_path + schema 初始化入口） | `acp_services.store.store.JobStore`（唯一实现；`JobStorePort` 端口已删） | `todo 77` | `todo 77`（已移除 `JobStorePort` 注入） | `tests/test_services_migration_equivalence.py` |
| `acp.scheduler.migrations.migrate`（版本化迁移入口） | `acp_services.store.migrations.migrate`（唯一实现；`MigrationsPort` 端口已删） | `todo 77` | `todo 77`（已移除 `MigrationsPort` 注入） | `tests/test_services_migration_equivalence.py` |
| `acp.catalog.method_levels_to_cli_flags`（参数转换 CLI flag choke point） | `acp_services.runtime.method_flags.method_levels_to_cli_flags`（唯一实现；旧路径同对象转发） | `todo 77` | `todo 77`（remote `script_gen.py:26` 改指 Services） | `tests/test_services_migration_equivalence.py` |
| `acp.calculations.step_requirements.evaluate_prerequisite`（步骤前置条件阻断） | `acp_services.runtime.step_requirements.evaluate_prerequisite`（唯一实现；`StepRequirementPort` 不再注入） | `todo 77` | `todo 77`（已移除 `ServicesPorts.step_requirements` 注入） | `tests/test_services_migration_equivalence.py` |

### 已关闭 vs 延后边界（本切片声明范围）

- **CLOSED（本切片）**：schema/store、migrations、参数转换 CLI flag choke point、plan/step requirements
  （含 `acp.calculations.contracts` / `step_result` 同实现转发；见上表）。
- **DEFERRED（保留行与移除条件，未标关闭）**：
  1. **控制与恢复**（`JobControlPort`/`JobSubmissionPort`/`JobEditPort`/`JobRegistrationPort`/
     `JobStateAdvancePort`）——按计划 todo 78 明确「过渡 controller 仍可由旧 ACP 装配（与独立 worker
     区分）」，实现体仍在 `acp.scheduler.manager.JobManager`；另批迁移。
  2. **Data writer/cache**（`RemoteStructureCache`/`TransferPipeline` 委托面）——委托已在 todos 11-15
     就位；随对象存储/读取面工作另批迁移。
  3. **`MethodConfigPort` catalog 归一化 choke point**（`normalize_and_validate_method_config`）——
     依赖 `catalog.py` 的 `FIELD_DEFINITIONS`/`METHOD_META`/`keyword_registry`，深耦合，随专用批次迁移。

> 上述延后项**不阻塞 todo 78 的 worker 独立性**：独立 worker 只需 envelope + StorageBinding +
> step requirements（本切片已 Services-native）+ CCCP 任务执行 + receipt 写入——不依赖 controller/
> manager、Data cache 或 catalog 归一化。

### DEFERRED 注入登记表（todo 82 复核，2026-10-19）

本表是 todo-77 残余注入的**显式 DEFERRED 登记**（不标关闭、不复制第二实现）。5 列恒定；
每行含保留原因、迁移任务、移除任务号或过渡边界、验收测试与移除条件。上方主表的对应移除列
已指向本节；状态 token 只在本表使用 `[DEFERRED]`，主表解析口径不变。

| 旧实现 | 目标模块 | 迁移任务 | 移除注入任务 | 验收 |
|---|---|---|---|---|
| `acp.scheduler.manager.JobManager`（pause/unpause/cancel/continue/rerun/purge） | `acp_services.contracts.injection.JobControlPort`；保留原因=控制状态机与进程信号仍在旧 controller，迁移属控制 owner 切片 | `todo 25` 至 `todo 26`（Runtime 用例已收敛，实现体仍在 `src/acp`） | [DEFERRED] T78 过渡 controller 边界：独立 worker 与过渡 controller 明确区分，controller 仍可由旧 ACP 装配；移除任务=后续 controller owner 迁移（`todo 77` 残余），条件=RuntimeCommands 直接拥有控制状态机且装配不再构造 `JobControlPort` | `tests/test_services_runtime_usecases.py`；`tests/test_acp_scheduler_processctl.py` |
| `acp.scheduler.manager.JobManager.submit` | `acp_services.contracts.injection.JobSubmissionPort`；保留原因=提交意图/目录分配/release 仍由旧 manager 串接 | `todo 25` 至 `todo 26`（提交入口经 Runtime 用例转发一次） | [DEFERRED] T78 过渡 controller 边界；移除任务=后续 controller owner 迁移（`todo 77` 残余），条件=submit 用例在 Services 内完成 intent→reserve→dispatch，且装配不再注入 `JobSubmissionPort` | `tests/test_services_runtime_usecases.py`；`tests/test_services_cli_full_loop.py` |
| `acp.scheduler.manager.JobManager.edit_recalculate` | `acp_services.contracts.injection.JobEditPort`；保留原因=编辑覆盖表与重算编排仍在旧 manager | `todo 25` 至 `todo 26`（edit 用例经端口转发） | [DEFERRED] T78 过渡 controller 边界；移除任务=后续 controller owner 迁移（`todo 77` 残余），条件=edit-coverage registry 归 Services 且装配不再构造 `JobEditPort` | `tests/test_services_runtime_usecases.py`；`tests/test_acp_job_edit.py` |
| `acp.scheduler.registration.register_completed_cli_job` | `acp_services.contracts.injection.JobRegistrationPort`；保留原因=CLI 完成作业登记仍写旧 registration 面 | `todo 25`（CLI 提交入口经 Runtime 用例） | [DEFERRED] T78 过渡 controller 边界；移除任务=后续 controller owner 迁移（`todo 77` 残余），条件=完成作业登记由 Services store 用例直接完成且装配不再注入 `JobRegistrationPort` | `tests/test_services_runtime_usecases.py`；`tests/test_services_cli_full_loop.py` |
| `acp.scheduler.store.JobStore.transition` | `acp_services.contracts.injection.JobStateAdvancePort`；保留原因=store 实现已 Services-native，但 controller 资格校验仍由旧 manager 持有 | `todo 31`（controller 校验回执资格后经 CAS 推进） | [DEFERRED] T78 过渡 controller 边界；移除任务=后续 controller owner 迁移（`todo 77` 残余），条件=Services controller 自己校验回执资格并调用 CAS，装配不再构造 `JobStateAdvancePort` | `tests/test_services_worker_contract.py`；`tests/test_services_runtime_usecases.py` |
| `acp.calculations.executor.CalculationPlanExecutor`（plan dispatch + resume） | `acp_services.contracts.injection.PlanExecutionPort`；保留原因=CCCP 任务执行适配仍在旧 executor，独立 worker 侧已另有 envelope 路径 | `todo 27` 至 `todo 31`（worker 只执行冻结 plan 并写回执） | [DEFERRED] `todo 78`（自足 worker 组合根）+ `todo 81`（生产执行入口）；移除任务=`todo 81` 落地后复核，条件=生产路径直接消费冻结 plan/envelope 且不再注入 `PlanExecutionPort` | `tests/test_calculation_executor.py`；`tests/test_services_worker_bootstrap.py` |
| `acp.results.remote_structure_cache.RemoteStructureCache` / `acp.calculations.result_publication.register_result_manifest` / `acp.results.frame_candidates.save_frame_candidate`（Data writer/cache） | `acp_services.data.resource_read` + `acp_services.data.transfer`（委托面）；保留原因=旧 Client/GET 兼容窗口与缓存发布仍需旧 writer 兜底 | `todo 11` 至 `todo 16`（Data 端口/持久 prepare/读取面已交付） | [DEFERRED] `todo 81`（生产 manifest/frame-candidate/structure-source 写入经 DataResourcePort）；移除任务=`todo 81`，条件=call-spy 证明生产路径不再直接调用旧 writer 且旧 GET 兼容语义保持 | `tests/test_services_data_port.py`；`tests/test_services_publication_data_port.py`；`tests/test_acp_api_v1.py` |
| `acp.catalog.normalize_and_validate_method_config`（catalog 归一化 choke point） | `acp_services.contracts.injection.MethodConfigPort`；保留原因=`catalog.py` 的 `FIELD_DEFINITIONS`/`METHOD_META`/`keyword_registry` 深耦合，属专用迁移切片 | `todo 32` 或 `todo 42`（CLI/catalog 参数转换一次切换；CLI flag choke point 已关闭） | [DEFERRED] `todo 77` 残余（未排期独立迁移切片）；移除条件=归一化判据与 keyword registry 移入 Services 且旧路径改同对象转发，未满足前不得改标 CLOSED | `tests/test_acp_catalog.py`；`tests/test_catalog_keyword_case.py`；`tests/test_services_injection_contracts.py` |

> **B06-2 worker 记（todo 31）**: 本阶段仅完成**端口收敛**——worker 侧 `src/acp_services/runtime/**`
> 只经显式 Protocol（`PlanVerificationPort` / `StepRequirementPort` / `TaskExecutorPort`）装配，
> 不 import `acp`、不触 jobs DB、不自增 attempt（AST 守护见 `tests/test_services_worker_contract.py`）。
> **worker 独立性不由本任务证明**，而由 todo 78（自足装配入口，新进程仅凭冻结数据构造依赖）证明；
> 上表三行的「旧实现」在 todo 77 / todo 78 实际迁入并移除注入前，仍属过渡装配。

> **B05-1 Runtime 用例记（todo 25）**: 本阶段新建 `src/acp_services/runtime/commands.py`
> （`RuntimeCommands`），把 submit/pause/unpause/cancel/continue/rerun/edit（+ purge + CLI
> 完成作业登记）表达为 **同一执行 owner 的 Runtime 用例**：每个用例经显式注入端口
> （`JobSubmissionPort`/`JobControlPort`/`JobEditPort`/`JobRegistrationPort`）**恰好转发一次**，
> 由旧 acp 装配入口 `src/acp/services_bootstrap.py::build_runtime_commands` 创建适配器并注入；
> Services 不 import/不动态查找旧实现。API `src/acp/api/v1_routes.py` 与 CLI `src/acp/cli.py`
> 的提交/控制入口改经 Runtime 用例（旧 `JobManager`/registration 方法保留为同实现转发）。
> **单一 owner 登记**：控制意图（pause/cancel/rerun/edit…）仍归 `acp.scheduler.manager.JobManager`，
> Runtime 用例只是入口，不是第二个状态机、不双写；见 `src/acp/scheduler/AGENTS.md`
> STATE TRANSITIONS。**本阶段只声明端口收敛，不声明 Services 已独立**；独立性由 todo 77 迁入
> 实现并移除注入后证明。

## 九类覆盖核对（评审 B1）

| 类别 | 行 |
|---|---|
| job store（db_path + schema 初始化入口） | 已关闭（见上节，B05-7） |
| migrations | 已关闭（见上节，B05-7） |
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

- `acp.scheduler.molecule_groups`（`resolve_molecule_key` 等）：**已裁决并迁移**（B05-7 / todo 77）：
  实现驻点迁至 `acp_services.store.molecule_groups`（随 schema/store 闭包唯一实现），旧路径
  `src/acp/scheduler/molecule_groups.py` 为同实现转发。`acp.scheduler.naming` 同理迁至
  `acp_services.store.naming`。
- 本节为**散文记录**，不进入上表解析（上表每行仍须含具体迁移/移除 todo 号）；
  机器可校验状态见 `tests/test_services_injection_contracts.py::test_registry_rows_carry_concrete_migration_and_removal`。

> **B06-6 worker 自足装配记（todo 78）**: `src/acp_services/worker_bootstrap.py` 提供独立 worker
> composition root（`assemble_worker` / `load_and_assemble` / `main`）：新进程仅凭冻结 envelope +
> 冻结 plan + StorageBinding + 只读工件构造依赖，不依赖父进程注入对象、不 import `acp`、不连 jobs DB。
> `PlanVerificationPort` 改由 Services-native `FrozenPlanVerifier`（复算冻结 plan body digest）实现——
> 上表「`acp.calculations.identity.compute_identity`（两层冻结 plan 身份复算）」行的移除任务 **todo 78
> 在独立 worker 路径关闭**；过渡 controller 仍可由旧 ACP 装配（计划明确区分独立 worker 与过渡 controller），
> 故该行不删。`StepRequirementPort` 由 `ServicesStepRequirements` 适配（todo 77 唯一实现）。
> 远端入口 `build_remote_worker_command` 指向 `python -m acp_services.worker_bootstrap`，不经旧 CLI；
> 整链接线归 todo 32。守护：`tests/test_services_worker_bootstrap.py`。
