# Services migration ledger — 逐符号迁移台账（B00-3 / todo 3）

**Created:** 2026-10-18，基线 HEAD `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9`（工作区含 lane A 前端改动）。
**Scope:** ACP Services 计划（`acp-parallel-plan-2-services`）中将被 `src/acp_services/` 接管的平台服务符号/能力。
**含义:** 本表只登记 **Services 侧平台能力**（Workspace/Runtime/Data/事件/入口/部署）。cccp 科学与任务基元的逐符号台账仍由
`tests/baseline/refactor-evidence/migration_ledger.md` 维护（本文件不改动它，也不与之混列数）。两表结构相同以便同一解析器复用，但内容域不同。

## 行格式（row format）

| 列 | 含义 |
|---|---|
| 能力 (capability) | Services 平台能力/符号名（如 ProjectManager） |
| 当前实现驻点 (current implementation station) | 该能力当前唯一实现体所在文件（含首个符号行号） |
| 兼容入口 (compat entry) | 迁移期保留的对外入口/重导出面 |
| 生产消费者 (production consumer) | 生产代码中调用该能力的路径（含行号） |
| 退出条件 (exit condition) | 本行可删除/收口的判据，含接收本计划任务号（todo N） |
| 验收测试 (acceptance test) | 守护该行状态的测试 |

## 行引用约定

- **6 列恒定**：本表所有数据行恰好 6 列；扩展字段（owner/prerequisites/provider/consumer/acceptance/base_commit/applied_commit）一律登记在
  `handoff_registry.md`，**不得**塞进本表同一行（评审 P1-5：拆表，不混列数）。
- **当前实现驻点**列必须给出 `src/....py` 路径；符号移动即改本列；退出条件满足才删行。
- **新能力先加行再落码**：任何 Services 新符号的落点应先在本表登记，再在 `src/acp_services/` 实现。
- 补丁状态在本表中不登记；「已交付」与「已集成」在 `handoff_registry.md` 的状态列分开登记。

## 台账

| 能力 | 当前实现驻点 | 兼容入口 | 生产消费者 | 退出条件 | 验收测试 |
|---|---|---|---|---|---|
| 项目/成员/分组 owner（ProjectManager） | `src/acp/scheduler/projects.py:47` | `acp.scheduler.projects.ProjectManager` | `src/acp/scheduler/manager.py:389`（装配）；`src/acp/scheduler/registration.py:142`；`src/acp/api/v1_routes.py`（项目端点） | Workspace owner 接管后旧类降为兼容转发；接收 todo 6（B01-1） | `tests/test_acp_projects.py` |
| 结构来源组织存储（StructureSourceStore） | `src/acp/scheduler/structure_source_store.py:361` | `acp.scheduler.structure_source_store.StructureSourceStore` | `src/acp/api/v2_structure_sources.py:103`；`src/acp/api/v1_routes.py:1508`；`src/acp/scheduler/manager.py:1899` | Data/Workspace 唯一写入口接管；接收 todo 6 或 todo 11 | `tests/test_acp_structure_source_store.py`；`tests/test_acp_api_v2_structure_sources.py` |
| 结构来源后台索引（StructureSourceIndexer） | `src/acp/scheduler/structure_source_indexer.py:48` | `acp.scheduler.structure_source_indexer.StructureSourceIndexer` | `src/acp/api/v2_structure_sources.py:105` | 持久 prepare/availability 接管；接收 todo 13 至 todo 14 | `tests/test_acp_structure_source_indexer.py` |
| 分子分组/别名（molecule_groups） | `src/acp/scheduler/molecule_groups.py:46` | `acp.scheduler.molecule_groups.resolve_molecule_key` | `src/acp/api/v2_routes.py`（group 端点）；`src/acp/scheduler/task_views.py` | Workspace owner 接管；接收 todo 6 或 todo 19 | `tests/test_acp_scheduler_molecule_groups.py` |
| 只读任务投影（task_views） | `src/acp/scheduler/task_views.py:215` | `acp.scheduler.task_views.query_project_tasks` | `src/acp/api/v2_routes.py:457`；`src/acp/api/v2_routes.py:460`（_enrich_active_rows 同步回退） | 查询改为只读已准备快照并去除同步扫描/解析回退；接收 todo 19 至 todo 20 | `tests/test_acp_scheduler_task_views.py`；`tests/test_acp_api_v2_task_view.py` |
| 任务编辑覆盖登记（job_edit） | `src/acp/scheduler/job_edit.py:126` | `acp.scheduler.job_edit.audit_workflow_edit_coverage` | `src/acp/api/v1_routes.py`（edit-recalculate 端点） | Runtime 控制用例接管且 EDIT_ACTIVE_WORKFLOWS 门等价；接收 todo 25 至 todo 26 | `tests/test_acp_job_edit.py` |
| 远端目录身份映射（paths） | `src/acp/scheduler/remote/paths.py:46` | `acp.scheduler.remote.paths.storage_relative_path` | `src/acp/scheduler/manager.py:1960`；`src/acp/scheduler/manager.py:3353`；`src/acp/scheduler/remote/runner.py:64` | StorageBinding 唯一分配权威接管；接收 todo 12 | `tests/test_remote_storage_identity.py` |
| 内容寻址发行（release） | `src/acp/scheduler/remote/release.py:685` | `acp.scheduler.remote.release.ensure_node_release` | `src/acp/scheduler/remote/runner.py:67`；`src/acp/scheduler/remote/runner.py:2072` | 发布/资产契约与整链切换接管；接收 todo 32 至 todo 33 | `tests/test_remote_code_release.py`；`tests/test_remote_code_release_binding.py` |
| 提交意图/租约（submission） | `src/acp/scheduler/remote/submission.py:56` | `acp.scheduler.remote.submission.submission_id_for` | `src/acp/scheduler/remote/runner.py:374`；`src/acp/scheduler/remote/script_gen.py:643` | ExecutionEnvelope/Receipt 与提交意图接管；接收 todo 30 至 todo 33 | `tests/test_remote_submission_protocol.py` |
| 远端 LSF 脚本入口（script_gen） | `src/acp/scheduler/remote/script_gen.py:150` | `acp.scheduler.remote.script_gen.build_remote_cli_command` | `src/acp/scheduler/remote/runner.py:71` | 一次切换远端入口并禁新 Services 动态转调旧 CLI；接收 todo 32 | `tests/test_remote_phase1.py`；`tests/test_remote_phase4.py` |
| 节点配置纯校验（remote/config） | `src/acp/scheduler/remote/config.py:260` | `acp.scheduler.remote.config.RemoteExecutionConfig` | `src/acp/scheduler/remote/runner.py`；`src/acp/init_wizard/sniff_remote.py` | 无 Paramiko 的节点配置校验提取完成；接收 todo 37 | `tests/test_remote_phase1.py` |
| 结果发布（result_publication） | `src/acp/calculations/result_publication.py:224` | `acp.calculations.result_publication.register_result_manifest` | `src/acp/calculations/executor.py:67`；`src/acp/calculations/primitives/scan.py:248`；`src/acp/calculations/primitives/irc.py:236` | 经 Data 公共入口发布/写 markers/读工件接管且保持 completed 时序；接收 todo 28 | `tests/test_calculation_executor.py` |
| v2 展示清单写入（storage/manifest） | `src/acp/storage/manifest.py:88` | `acp.storage.manifest.ResultManifest` | `src/acp/calculations/result_publication.py:231` | 科学产品 frames/annotation adapter 接管；接收 todo 28 | `tests/test_storage_manifest_kinds.py` |
| v2 展示清单读取（results/manifest） | `src/acp/results/manifest.py:23` | `acp.results.manifest.load_result_manifest` | `src/acp/calculations/batch/`（loader）；`src/acp/api/`（detail 投影） | Data 统一读取面接管（只读、零 SSH/SFTP）；接收 todo 15 | `tests/test_storage_manifest_kinds.py` |
| 候选权威文件（frame_candidates） | `src/acp/results/frame_candidates.py:92` | `acp.results.frame_candidates.save_frame_candidate` | `src/acp/api/v1_routes.py:4283` | 科学产品与帧 adapter 接管；接收 todo 28 | `tests/test_acp_frame_candidates.py`；`tests/test_acp_api_frame_candidates.py` |
| 远端结构缓存（remote_structure_cache） | `src/acp/results/remote_structure_cache.py:126` | `acp.results.remote_structure_cache.RemoteStructureCache` | `src/acp/scheduler/manager.py:2018`；`src/acp/api/v2_routes.py:215` | 进程内预取升级为持久 prepare operation 且缓存发布接管；接收 todo 13 至 todo 14 | `tests/test_acp_remote_catalog_prefetch.py` |
| API 读取根（_job_read_root） | `src/acp/api/v1_routes.py:366` | `acp.api.v1_routes._job_read_root`（镜像 `src/acp/api/v2_routes.py:220`） | `src/acp/api/v1_routes.py:2564`；`src/acp/api/v2_routes.py:256` | 统一读取根/只读快照接口接管（零 SSH/SFTP、零 enqueue）；接收 todo 15 | `tests/test_acp_api_v1.py`；`tests/test_acp_api_v2.py` |
| API 事件流/SSE（stream_job_events） | `src/acp/api/v1_routes.py:5747` | `acp.api.v1_routes.stream_job_events`（legacy_stream_job_events） | frontend Workbench（SSE 客户端） | 持久事件协议/授权复用流接管；接收 todo 22 至 todo 24 | `tests/test_api_sse_backlog.py` |
| 每任务事件日志（JobEventLog） | `src/acp/scheduler/events.py:93` | `acp.scheduler.events.JobEventLog` | `src/acp/api/v1_routes.py:530`；`src/acp/scheduler/manager.py`（状态转换事件） | 统一事件协议与持久化顺序/幂等重放接管；接收 todo 22 至 todo 23 | `tests/test_scheduler_events_incremental.py` |
| 持久/CAS 存储（JobStore） | `src/acp/scheduler/store.py:106` | `acp.scheduler.store.JobStore`（旧 schema 兼容读） | `src/acp/scheduler/manager.py`；`src/acp/api/v1_routes.py` | owner repository/schema 迁移接管；接收 todo 77 | `tests/test_acp_scheduler.py` |
| 任务控制面（JobManager pause/unpause/cancel/continue/rerun/purge） | `src/acp/scheduler/manager.py:2448` | `acp.scheduler.manager.JobManager` | `src/acp/api/v1_routes.py`（控制端点）；CLI 控制命令 | Runtime 用例与规则提取后接管同一执行 owner；接收 todo 25 至 todo 26 | `tests/test_acp_scheduler_processctl.py` |
| 计划执行器（CalculationPlanExecutor） | `src/acp/calculations/executor.py:690` | `acp.calculations.executor.CalculationPlanExecutor` | `src/acp/workflows/simple.py` | worker 只执行冻结 plan 并写回执后接管；接收 todo 27 至 todo 31 | `tests/test_calculation_executor.py` |
| 步骤前置条件阻断（evaluate_prerequisite） | `src/acp/calculations/step_requirements.py:170` | `acp.calculations.step_requirements.evaluate_prerequisite` | `src/acp/calculations/executor.py:902` | 科学闭包/依赖阻断与 resume_source 接管；接收 todo 27 | `tests/test_calculation_executor.py` |
| 批量执行引擎（BatchOptimizeEngine） | `src/acp/calculations/batch/engine.py:119` | `acp.calculations.batch.engine` | `src/acp/workflows/batch_optimize.py` | plan/step identity 与恢复接管；接收 todo 27 或 todo 77 | `tests/test_batch_optimize.py` |
| 参数转换（catalog normalize_and_validate_method_config） | `src/acp/catalog.py:4875` | `acp.catalog.normalize_and_validate_method_config` | `src/acp/api/v1_routes.py`；`src/acp/scheduler/jobs.py` | CLI/catalog 参数转换一次切换且大小写折叠三 choke point 等价；接收 todo 32 或 todo 42 | `tests/test_acp_catalog.py`；`tests/test_catalog_keyword_case.py` |
| 统一 CLI 入口与 dispatch（acp run） | `src/acp/cli.py:4129` | `acp.cli.main`（console script `acp` / `python -m acp`） | pyproject console script；`src/acp/scheduler/runner.py`（子进程入口） | CLI/Web 共用 Services 用例且旧参数语义保留；接收 todo 35 或 todo 74 | `tests/test_acp_cli.py` |
| 初始化向导（run_init） | `src/acp/init_wizard/flows.py:348` | `acp.init_wizard.flows.run_init`（`acp init`） | `src/acp/cli.py:4102` | 统一 init：使用方式/档位/组件/计划应用/doctor；接收 todo 36 | `tests/test_acp_init_cli.py`；`tests/test_acp_init_persist.py` |
| 历史 manifest 只读兼容（compat/legacy） | `src/acp/compat/legacy/manifests.py:111` | `acp.compat.legacy.manifests.read_s2_path_manifest` | `src/acp/results/`（历史任务读取）；`src/acp/compat/legacy/layouts.py` | 历史 reader 逐项关闭并同步契约；接收 todo 70 | `tests/test_legacy_layout_compat.py` |
| 工作流注册表（WorkflowRegistryEntry） | `src/acp/workflows/registry.py:15` | `acp.workflows.registry.list_workflow_entries` | `src/acp/cli.py`（子命令映射）；`src/acp/api/`（catalog 端点） | CLI/Web 共用 Services 用例后收口；接收 todo 35 | `tests/test_core_workflow.py`；`tests/test_acp_workflow_task_matrix.py` |

## 覆盖说明

初版覆盖 30 行（≥20）。必需覆盖面逐项对应：

- projects → 行 1；structure_source_store → 行 2；task_views → 行 5；
- remote paths/release/submission → 行 7/8/9；calculations 发布 → 行 12；
- api 读取 → 行 17；init_wizard → 行 28；catalog → 行 26；
- 另含 storage/manifest、results/frame_candidates、remote_structure_cache、scheduler/store、manager 控制面、calculations/executor、calculations/step_requirements、api 事件/SSE、scheduler/events、remote/script_gen、CLI dispatch、batch engine、compat/legacy、workflows registry。

## 与 cccp 台账的边界

本表**不**登记 `cccp.**` 科学基元（run_singlepoint 等）；那由
`tests/baseline/refactor-evidence/migration_ledger.md` 单独维护。两表共用 6 列格式以便同一 checker 解析，但 checker 路径不同、内容域不重叠（评审 P1-5 的拆表要求）。

## 追加规则（append-only semantics）

- 本表**只增不删**：新增 Services 能力先加行；移动实现体只改「当前实现驻点」列；退出条件满足才删行，删行须在 `handoff_registry.md` 的 `applied_commit` 记录理由。
- 行内不得出现 `|`（会破坏表格解析）；枚举用「或」连接，代码中的 `X | None` 写作 `X-or-None`。
