# acp/ — ACP Unified Module

## OVERVIEW
The unified `acp` CLI, stage-based workflow pipeline, capability-driven QC backends, and generic core models. ≈289 files (incl. API + scheduler + nmr + init_wizard). ACP is the workflow/planning/persistence layer (fourth layer) on top of the `cccp` QC base: task cores live in `cccp/calculation/tasks/`, capability backends in `cccp/backends/` — `acp.calculations.primitives` and `acp.backends` are pure compat forward/re-export shims plus ACP publish contracts (see root AGENTS.md ANTI #17). Platform services (store/schema, Data prepare/read, Runtime use cases, access boundary) live in `src/acp_services/`; the old `acp` paths for migrated symbols are **identity forwarders** (`is` same object), never second implementations (root ANTI #29–#32).

**可运行工作流（15，registry 驱动）**: Confsearch, PESsearch, BatchOptimize, XtbPathSearch, OrcaGradient, XtbConstrainedOpt, irc, scan, tsmode, casscf, nmr, singlepoint, optimize, frequency, xtb_optimize（XtbPathSearch/OrcaGradient/XtbConstrainedOpt 消费冻结 `pes2ts_*_request_v1` payload，本地专用）。Retired（catalog `status:"retired"`，仅历史作业展示）: ensemble, energy, xtbmd_censo_energy, mechanism, conformer, benchmark, mech-conf, mech-step, mech-confirm, mech-chain, optfreq, optfreqsp, Lowconfirm, Highconfirm。

**Catalog/registry 计数（2026-10-26 核验）**: `WORKFLOW_CATALOG` 30 条 = 15 active + 14 retired + 1 planned（`custom_sequence`）；`workflows/_WORKFLOW_REGISTRY` 16 条（含内部合成 `fake`）；CLI 退役拦截 `_CLI_REMOVED_WORKFLOWS` 12 名；catalog-retired `conformer`/`benchmark` 无 parser（argparse invalid choice）。

## STRUCTURE
```
acp/
├── cli.py              # argparse 统一入口：run/doctor/init/task/mcp（`serve` 仅 `acp run serve`）；retired 子命令拦截报错；--json/--dry-run 仅 _MACHINE_OUTPUT_WORKFLOWS
├── __init__.py          # `__version__` via importlib.metadata.version("acp")（fallback "0.3.0"）；__all__=["__version__"]
├── __main__.py          # `python -m acp` works
├── init_wizard/         # `acp init` 向导（13 .py / 4418 L）：flows.py 729（资源菜单 + orchestration，run_init）、plan.py 548（InitPlan/build_plan/apply_plan/render_plan/PLAN_VERSION/SecretRef）、newnode.py 804（新节点 TOFU 自建 SSHClient + NodeManager.bootstrap_node）、options.py 385（UsageMode/TIER_SPECS/COMPONENT_SPECS/tier_enablement）、capabilities.py 280（PASS/FAIL/NOT_VERIFIED 三态 doctor）、lab_doctor.py 204（LabEnvironment→acp_services.runtime.lab_capabilities）、gating.py 143（→contracts.action_gating）、persist.py 228（merge-safe atomic YAML+.bak→contracts.config_plan）、answers/prompts/sniff_local/sniff_remote；__init__ 仅 re-export
├── services_bootstrap.py             # 过渡装配根（320 L）：build_services_ports/build_runtime_commands/build_runtime_recovery
├── services_data_bootstrap.py        # DataResourcePort adapters + build_read_root_resolver/build_cache_only_resource_reader（2470 L）
├── services_completion_bootstrap.py  # attempt completion seam（115 L）：build_attempt_completion_service
├── services_events_bootstrap.py      # JobEventLogPort + outbox delivery（161 L）
├── services_mcp_bootstrap.py         # build_mcp_runtime（409 L；`acp mcp serve` 组合根）
├── services_publication_bootstrap.py # PublicationDataPort + manifest publisher（256 L）
├── catalog.py           # WORKFLOW_CATALOG（30 条）+ METHOD_META + METHOD_SCHEMAS（5270 行；29 schema，含 pes_bond_scan/xtb_path_search/orca_gradient/xtb_constrained_opt；electronic-state/CASSCF 路由块）
├── confsearch/          # 4 协议构象搜索：engine, contracts, manifest, profiles, selection, protocols/（xtb-crest/xtb-md/censo-crest/xtbmd-censo）, shared/, sampling.py + sampling_models.py
├── calculations/        # 规划/发布层：contracts/checkpoint/executor/plans/identity + attempt_freeze（1235 L）/attempt_publication（522 L）+ primitives/（纯兼容转发 → cccp.calculation 任务核心 + ACP 发布契约）+ pes/（含 scan_snapshot/path_selection/atom_selection/outputs/review/candidates）+ batch/（options.py 方法/基组选项归一化 + effective_config.py）+ irc/ + tsmode/
├── compat/              # 只读：legacy/ 历史 manifest 读取器 + 布局双探针
├── results/             # result_manifest 读取 + frames（TrajectoryFrame/VIEW_REGISTRY 9 视图）+ frames_adapter（ScienceProduct→帧，HN-02）+ sampling_graph + frame_candidates + frame_candidate_geometry + frame_candidate_store + structure_viewer + irc_projection + remote_structure_cache + orca_parser + frequencies
├── storage/             # result_manifest v2 写入（ProductKind 15 kinds，含 pes_profile/irc_endpoint/thermo_report/multireference_report/wavefunction/spin_diagnostics/active_space/state_comparison）
├── core/                # 通用机制：Structure, WorkflowRunner, Registry, State, Config
├── backends/            # 纯 re-export shim（→ cccp.backends）+ 隔离的 legacy batch 面
├── chem/                # RDKit embedding + composition
├── intake/              # 数据摄入：models, parsers（6 格式）, storage
├── io/                  # StructureReader / StructureWriter（thin cccp wrapper）
├── workflows/           # pes_search/batch_optimize/irc/simple/tsmode/xtb_path/xtb_constrained_opt/orca_gradient/nmr + registry（legacy 退役引擎仍作 Confsearch 协议引擎）
├── nmr/                 # 转发面 → cccp.analysis.nmr（见 nmr/AGENTS.md）；仅 analysis_revision.py（463 L）+ method_config.py（catalog coupling）保留 acp 侧代码，其余为 re-export shim
├── api/                 # FastAPI：server/routes/v1_routes/v2_routes/v2_structure_sources/schemas + mechanism_readonly（历史只读）
└── scheduler/           # jobs/manager/runner/store/stage_tasks/tasks/task_views/molecule_groups/structure_source_store/structure_source_indexer/job_edit + remote/（LSF 远程执行）
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| CLI entry | `cli.py` | `acp run` dispatch + `_build_config()` → `cccp.config.load_config`；顶层子命令 **run/doctor/init/task/mcp**（`serve` 仅 `acp run serve`）；`acp task` = ingest\|plan\|execute\|query\|pause\|unpause\|continue\|cancel\|rerun\|recover\|export（`--run-root` / `--controller <url>`，controller-targeted client 从不启动 Controller）；`acp mcp serve` = `--transport stdio`（唯一支持值）+ `--controller`，委派 `acp_services.entrypoints.mcp_stdio` + `services_mcp_bootstrap.build_mcp_runtime`；`--json`/`--dry-run` 仅 `_MACHINE_OUTPUT_WORKFLOWS`（singlepoint/optimize/frequency/scan/casscf） |
| Init wizard | `init_wizard/flows.py` | `acp init` → `cli._handle_init` → `run_init(config, answers_path, plan_only)`；plan T8 资源菜单 + orchestration |
| Services composition roots | `services_bootstrap.py` + `services_{data,completion,events,mcp,publication}_bootstrap.py` | acp 侧 6 个过渡装配根：ServicesPorts/RuntimeCommands、DataResourcePort + read_root/cache-only reader、attempt completion、event/outbox、`build_mcp_runtime`、publication data port/manifest publisher |
| Workflow catalog | `catalog.py` | 5270 行；`WORKFLOW_CATALOG`（30 = 15 active + 14 retired + 1 planned `custom_sequence`）, `SUPPORTED_WORKFLOWS`, `METHOD_META`, `METHOD_SCHEMAS`（29，含 `pes_bond_scan`/`xtb_path_search`/`orca_gradient`/`xtb_constrained_opt`） |
| Workflow registry | `workflows/registry.py` | CLI subcommand → `WorkflowSpec` builder mapping；`_WORKFLOW_REGISTRY` 16 条（含内部合成 `fake`） |
| Core models | `core/models.py` | Structure, StructureRecord, StructureEnsemble, JobSpec |
| Core workflow | `core/workflow.py` | WorkflowSpec, WorkflowRunner, Stage, WorkflowResult |
| State persistence | `core/state.py` | WorkflowState, EventLog (JSONL) |
| Backend protocols | `cccp/backends/base.py`（shim: `backends/base.py`） | GeometryOptimizer / SinglePointCalculator / ConformerSearcher / ... (PEP 544)；实现体在 cccp |
| Backend registry | `cccp/backends/registry.py`（shim: `backends/registry.py`） | `get_backend(name)`, `require_backend(capability)` |
| Confsearch engine | `confsearch/engine.py` | 4 protocols: xtb-crest/xtb-md/censo-crest/xtbmd-censo |
| Confsearch manifest | `confsearch/manifest.py` | `confsearch_manifest.json` handoff artifact (S1) |
| Confsearch profiles | `confsearch/profiles.py` | light / default / high resource profiles |
| Confsearch selection | `confsearch/selection.py` | Candidate selection and ranking logic |
| Confsearch sampling | `confsearch/sampling.py` + `sampling_models.py` | parse_traj_frames / assign_basins / SamplingSaturation frozen dataclasses |
| Calculation contracts | `acp_services/runtime/plan_contracts.py`（forwarder: `calculations/contracts.py`） | CalculationPlan, CalculationRequest, Checkpoint frozen dataclasses — old path re-exports the **same objects** (todo 77) |
| Plan executor | `calculations/executor.py` | CalculationPlanExecutor — step dispatch + checkpoint resume |
| Scientific identity v2 | `calculations/identity.py` | `compute_identity()` → `plan_identity` + per-step `step_identities`；`config_digest`/`identity_fingerprint` re-export 自 `cccp.analysis.identity`（T53 split） |
| Attempt freeze | `calculations/attempt_freeze.py` | `build_singlepoint_freeze_spec`, `build_local_release`, `local_release_identity` — frozen attempt 产物组 |
| Attempt publication | `calculations/attempt_publication.py` | `AttemptResultPublication.publish()` — 读 receipt + `WORK/00_RUNTIME/task_result.json` → `publish_result`（不写第二份 manifest） |
| Primitives | `cccp/calculation/tasks/`（compat: `calculations/primitives/`） | 实现体在 cccp 任务层；acp 侧为纯转发 + ACP 发布契约（run_singlepoint/optimize/frequency/scan/irc + casscf CASSCF/NEVPT2） |
| PES engine | `calculations/pes/engine.py` | PesSearchEngine: confsearch manifest → scan → candidates |
| PES contracts | `calculations/pes/contracts.py` | PesScanRequest, ScanCoordinate, EnergyProfile, CandidateRecommendation |
| PES validation | `calculations/pes/validation.py` | Topology guards, bond graphs, risky contacts, scan trajectory validation |
| PES atom mapping | `calculations/pes/atom_mapping.py` | RDKit MCS atom mapping, AtomIdentityMap |
| PES bond changes | `calculations/pes/bond_changes.py` | BondChange, compute_bond_changes, suggest_coordinate_plan |
| PES path analysis | `calculations/pes/path_analysis.py` | PathFrameEvidence, PathProfile, arclength, RMSD, energy derivatives |
| Batch engine | `calculations/batch/engine.py` | BatchOptimizeEngine — per-item Opt/TS + freq + SP + thermochemistry |
| Batch models | `calculations/batch/models.py` | TAG parsing (TS/INT + candidate_id), BatchStructureItem/Manifest, loaders |
| Checkpoint | `calculations/checkpoint.py` | write_checkpoint / load_checkpoint — atomic JSON with plan fingerprint validation |
| IRC | `calculations/irc/` | run_irc() + validation (connectivity, fingerprint, RMSD) |
| TS Mode | `calculations/tsmode/` | 选虚频→读源 Hessian→定向 OptTS→频率→验证报告 |
| Simple workflows | `workflows/simple.py` | singlepoint/optimize/frequency/scan/xtb-opt |
| XtbConstrainedOpt | `workflows/xtb_constrained_opt.py` | 744 L；`run_xtb_constrained_opt()` 薄封套 → `cccp.calculation.tasks.xtb_constrained_opt`（frozen `pes2ts_xtb_constrained_opt_request_v1`） |
| NMR workflow | `workflows/nmr.py` | Conformer search → GIAO → Boltzmann averaging → DP4/DP5；直连 `cccp.calculation.tasks.{conformer_search,censo_refine,nmr_shielding}`（TaskContext/typed contracts） |
| Compat legacy | `compat/legacy/manifests.py` | Read-only adapters: read_s2_path_manifest, read_s3_lowconfirm_manifest, etc. — retained by design (ANTI #18/#31); callers in `public_shim_inventory.md` |
| Compat layout | `compat/legacy/layouts.py` | find_study_layout, find_reaction_json — v2 + legacy dual-probe resolution |
| NMR | `nmr/` | 转发面 → `cccp.analysis.nmr`（DP4/DP5, averaging, scaling, FCHL, spectra, report）；仅 `analysis_revision.py` + `method_config.py` 保留 acp 侧代码（见 nmr/AGENTS.md） |
| Sampling projection | `results/sampling_graph.py` | build_sampling_energy_graph (view_type "sampling"); series energy-vs-time |
| ORCA parser | `results/orca_parser.py` | OrcaOutputParser → mode_frequencies/vectors/ir_intensities; zero modes kept |
| Frequencies | `results/frequencies.py` | build_normal_modes_product + build_frequency_report; corrupt modes skipped |
| Result manifest (read) | `results/manifest.py` | Unified `result_manifest.json` reader |
| Result manifest (write) | `storage/manifest.py` | Unified v2 writer (design doc §8)；`ProductKind` 15 kinds（含 pes_profile / irc_endpoint / thermo_report / multireference_report / wavefunction / spin_diagnostics / active_space / state_comparison） |
| TrajectoryFrame | `results/frames.py` | VIEW_REGISTRY (9 view_types) + to_node()/to_annotation() |
| Frames adapter | `results/frames_adapter.py` | 316 L；ScienceProduct → `TrajectoryFrame.to_node()/to_annotation()`，HN-02 frozen handoff（`PRODUCT_ADAPTER_RECORDS`） |
| Frame candidates | `results/frame_candidates.py` + `_store.py` + `_geometry.py` | Authority file `RESULT/frame_candidates.json`；写路径经 `acp_services.data.helpers.atomic_write_text`（Data 单一实现） |
| Structure viewer | `results/structure_viewer.py` | Per-workflow resolvers + overlay (MCS + Kabsch RMSD) |
| IRC projection | `results/irc_projection.py` | Two-direction energy series in strict file order |
| Remote structure cache | `results/remote_structure_cache.py` | Flat cache + pending_fetch + sweep_expired; singleton owned by JobManager |
| API v1 | `api/v1_routes.py` | Jobs, files, detail, recovery matrix, edit-recalculate, PES review; new read-only `GET /jobs/{id}/availability` + `GET /jobs/{id}/prepared/{resource}`; legacy GET retained for F0 (closure L2-02/L2-03); control endpoints go through `acp_services.runtime.commands.RuntimeCommands` |
| API v2 | `api/v2_routes.py` | Task views, batch ops, molecule groups, lineage |
| API schemas | `api/schemas.py` / `v1_schemas.py` | Pydantic models: QueueCounts, V1JobDetailResponse, V1JobPurgeRequest |
| API server | `api/server.py` | FastAPI app factory + static frontend hosting at `/` |
| Structure sources v2 | `api/v2_structure_sources.py` | GET/PATCH structure-source entries + facets + batch metadata |
| Scheduler tasks | `scheduler/tasks.py` | custom_name/name_revision (NOT in _SYNC_COLUMNS) |
| Task views | `scheduler/task_views.py` | Group/filter/sort/facets engine |
| Molecule groups | `scheduler/molecule_groups.py` | Alias resolution, merge suggestions |
| Structure-source store | `scheduler/structure_source_store.py` | 4 tables, source_uid_for(), upsert, RevisionConflictError |
| Structure-source indexer | `scheduler/structure_source_indexer.py` | Daemon: paged backfill + 60s incremental sweep |
| Job edit | `scheduler/job_edit.py` | EDIT_ACTIVE_WORKFLOWS registry, edit draft, preview fingerprint |
| Job manager | `scheduler/manager.py` | 7051 L；Lifecycle: pause/unpause/continue/rerun/purge + structure_cache singleton; prepare executor from `services_data_bootstrap`, outbox from `services_events_bootstrap`, attempt completion/publication seams |
| Job runner | `scheduler/runner.py` | Background process execution; `python -m acp.cli run <workflow>` subprocess |
| Jobs model | `scheduler/jobs.py` | JobSpec, JobState, SUPPORTED_WORKFLOWS derived from active catalog |
| Store | `scheduler/store.py` | Compat forwarder → `acp_services.store.store` (`JobStore is` same object); SQLite CAS, counts(), queue operations |
| Provenance | `scheduler/provenance.py` | Event sourcing, audit logging |
| Artifacts | `scheduler/artifacts.py` | Artifact tracking for stage workflows |
| Stage tasks | `scheduler/stage_tasks.py` | Plan providers mapping workflow → stage list |
| Local cleanup | `scheduler/local_cleanup.py` | Retention-based local disk cleanup |
| Migrations | `scheduler/migrations.py` | Compat forwarder → `acp_services.store.migrations` (`migrate is` same object); schema migrations incl. 017 custom_name, 018 job_edit_operations |
| Remote execution | `scheduler/remote/` | LSF bsub/bjobs, SSH/SFTP, code sync, result fetch (see remote/AGENTS.md) |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` — **新代码一律 PEP 604**
- **Docstrings**: compact single-line/短块，随所在包；cccp 侧 Google 风格
- **Backend design**: Capability Protocols (PEP 544)；实现体在 `cccp/backends/`（无 subprocess，委托 `cccp.qc.interfaces`）；`acp/backends/*` 仅纯 re-export shim
- **No chem logic in core/**: core/ only generic mechanism（Structure, WorkflowRunner, Registry）
- **Stage pipeline**: WorkflowSpec 组装 Stage 函数；WorkflowRunner 顺序执行
- **Backend layering**: workflows 调 `get_backend(...)(...)`（经 cccp.backends 兼容面或直连），不直接用 `cccp.qc.interfaces`
- **Task execution**: 单步 plan 经 `CalculationPlanExecutor` dispatch 到 `cccp.calculation.run_*`；多步走 BatchOptimizeEngine（底层 `cccp.calculation.batch`）— 禁在 acp 侧新增任务实现体
- **`__all__`**: 每个 `__init__.py` re-export public symbols
- **Job PAUSED 语义**: active 非终态（计入队列、禁删），poller 跳过；本地 killpg SIGSTOP/SIGCONT，远端 bstop/bresume；restart 本地 → FAILED `[RESTART_FAILED]`，远端保持 PAUSED
- **Task custom names**: `tasks.py` custom_name/name_revision；NOT in `_SYNC_COLUMNS`；`update_custom_name()` 事务性 revision check；PATCH `/api/v2/tasks/{id}` + expected_name_revision（409）
- **Structure-source org store**: `structure_source_store.py` — 4 tables；`source_uid_for()` = `ss_`+sha256；upsert 只碰 discovery fields；`RevisionConflictError`
- **Job-edit coverage**: `job_edit.py::EDIT_ACTIVE_WORKFLOWS` 必须同步；`audit_workflow_edit_coverage()` 守护
- **Services forwarders**: migrated symbols (`scheduler/{store,migrations}`, `calculations/{contracts,step_result,step_requirements}`, `catalog.method_levels_to_cli_flags`) re-export the **same object** from `acp_services`; `acp → acp_services` only. Never add business logic to a forwarder (root ANTI #29–#30)

## ANTI-PATTERNS（acp 模块特有，全局规则见 root AGENTS.md）
- **Thin wrapper syndrome**: acp/io 和 acp/core/config 大量委托 cccp — 增加抽象但逻辑薄
- **Depends on cccp**: acp imports `cccp.config`, `cccp.io`, `cccp.qc.interfaces` — intentional coupling
- **`# pyright:` suppressions**: `calculations/`（22 文件）最密，`workflows`/`scheduler`/`results` 次之；pyright 不在工具链
- **Bare `except Exception:`**: `scheduler/manager.py`（44 bare）与 `api/v1_routes.py`（19 bare）最密 — 多数有日志，少数静默吞错（root #8）
- **HARTREE_TO_KCAL duplication**: 4 处定义 — `acp/core/models.py`、`acp/results/energy_graph.py`、`cccp/utils/constants.py`、`cccp/analysis/pes/path_analysis.py`（历史截断值 `627.509`）；新常量禁复制
- **Retired catalog entries retained**: ensemble/benchmark/energy 等保留 `status:"retired"` + `visible:False`；NMR 已于 P1a 重新激活（`status:"active"`）
- **Forwarder re-implementation**: putting logic back into `scheduler/store.py`, `scheduler/migrations.py`, `calculations/{contracts,step_result,step_requirements}.py`, or `catalog.method_levels_to_cli_flags` breaks the single implementation root; they must stay pure identity re-exports
- **Deleting compat surfaces for line count**: `calculations/primitives`, `backends`, `compat/legacy`, retired engine shims and recovery fixtures are retained (root ANTI #31)

## RELEASE ROLES (B25b, 2026-10-23)
- The installed `acp-web` wheel is a first-class entry: `acp` console script and `acp mcp serve` both resolve from venv site-packages, not this checkout.
- Compat imports stay importable from the wheel: `acp.calculations.primitives`, `acp.backends`, `acp.compat.legacy.manifests`; `acp` forwards store/contracts to `acp_services` as the same object (see root ANTI #30).
- Root `acp` and the three role distributions are mutually exclusive install choices; do not mix their same-path files.
- Release/install evidence: `reports/acp2_implementation/bb45a46/B/B25/`; scenario `scripts/acceptance/acp2_package_install.py`.
