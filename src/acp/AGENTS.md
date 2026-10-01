# acp/ — ACP Unified Module

## OVERVIEW
The unified `acp` CLI, stage-based workflow pipeline, capability-driven QC backends, and generic core models. ≈238 files, ≈103k lines (incl. API + scheduler + nmr). Coexists with the underlying `cccp` package (Computational Chemistry Connection Package — the QC interface library).

**可运行工作流（12，registry 驱动）**: Confsearch, PESsearch, BatchOptimize, irc, scan, tsmode, casscf, nmr, singlepoint, optimize, frequency, xtb_optimize。Retired（catalog `status:"retired"`，仅历史作业展示）: ensemble, energy, xtbmd_censo_energy, mechanism, conformer, benchmark, mech-conf, mech-step, mech-confirm, mech-chain, optfreq, optfreqsp, Lowconfirm, Highconfirm。

## STRUCTURE
```
acp/
├── cli.py              # argparse 统一入口：run/doctor/serve；retired 子命令拦截报错
├── __init__.py          # Package docstring only
├── __main__.py          # `python -m acp` works
├── catalog.py           # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS（≈4.3k 行；electronic-state/CASSCF 路由块）
├── confsearch/          # 4 协议构象搜索：engine, contracts, manifest, profiles, selection, protocols/（xtb-crest/xtb-md/censo-crest/xtbmd-censo）, shared/, sampling.py + sampling_models.py
├── calculations/        # 计算基元唯一驻点：contracts/checkpoint/executor/plans + primitives/（sp/opt/freq/scan/irc/casscf/thermochemistry）+ pes/ + batch/ + irc/ + tsmode/
├── compat/              # 只读：legacy/ 历史 manifest 读取器 + 布局双探针
├── results/             # result_manifest 读取 + frames（TrajectoryFrame/VIEW_REGISTRY）+ sampling_graph + frame_candidates + frame_candidate_geometry + frame_candidate_store + structure_viewer + irc_projection + remote_structure_cache + orca_parser + frequencies
├── storage/             # result_manifest v2 写入（含 electronic-state product kinds）
├── core/                # 通用机制：Structure, WorkflowRunner, Registry, State, Config
├── backends/            # 能力 Protocol 适配层（ORCA/CREST/xTB/CENSO/Isostat/Molclus/external）
├── chem/                # RDKit embedding + composition
├── intake/              # 数据摄入：models, parsers（6 格式）, storage
├── io/                  # StructureReader / StructureWriter（thin cccp wrapper）
├── workflows/           # pes_search/batch_optimize/irc/simple/tsmode + registry（legacy 退役引擎仍作 Confsearch 协议引擎）
├── nmr/                 # DP4/DP5、平均、缩放/归属、FCHL、谱图、报告（13 模块；见 nmr/AGENTS.md）
├── api/                 # FastAPI：server/routes/v1_routes/v2_routes/v2_structure_sources/schemas + mechanism_readonly（历史只读）
└── scheduler/           # jobs/manager/runner/store/stage_tasks/tasks/task_views/molecule_groups/structure_source_store/structure_source_indexer/job_edit + remote/（LSF 远程执行）
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| CLI entry | `cli.py` | `acp run` dispatch + `_build_config()` → `cccp.config.load_config` |
| Workflow catalog | `catalog.py` | `WORKFLOW_CATALOG`（active+retired）, `SUPPORTED_WORKFLOWS`, `METHOD_META`, `METHOD_SCHEMAS` |
| Workflow registry | `workflows/registry.py` | CLI subcommand → `WorkflowSpec` builder mapping |
| Core models | `core/models.py` | Structure, StructureRecord, StructureEnsemble, JobSpec |
| Core workflow | `core/workflow.py` | WorkflowSpec, WorkflowRunner, Stage, WorkflowResult |
| State persistence | `core/state.py` | WorkflowState, EventLog (JSONL) |
| Backend protocols | `backends/base.py` | GeometryOptimizer / SinglePointCalculator / ConformerSearcher / ... (PEP 544) |
| Backend registry | `backends/registry.py` | `get_backend(name)`, `require_backend(capability)` |
| Confsearch engine | `confsearch/engine.py` | 4 protocols: xtb-crest/xtb-md/censo-crest/xtbmd-censo |
| Confsearch manifest | `confsearch/manifest.py` | `confsearch_manifest.json` handoff artifact (S1) |
| Confsearch profiles | `confsearch/profiles.py` | light / default / high resource profiles |
| Confsearch selection | `confsearch/selection.py` | Candidate selection and ranking logic |
| Confsearch sampling | `confsearch/sampling.py` + `sampling_models.py` | parse_traj_frames / assign_basins / SamplingSaturation frozen dataclasses |
| Calculation contracts | `calculations/contracts.py` | CalculationPlan, CalculationRequest, Checkpoint frozen dataclasses |
| Plan executor | `calculations/executor.py` | CalculationPlanExecutor — step dispatch + checkpoint resume |
| Primitives | `calculations/primitives/` | run_singlepoint/optimize/frequency/scan/irc + casscf.py CASSCF/NEVPT2 |
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
| NMR workflow | `workflows/nmr.py` | Conformer search → GIAO → Boltzmann averaging → DP4/DP5 |
| Compat legacy | `compat/legacy/manifests.py` | Read-only adapters: read_s2_path_manifest, read_s3_lowconfirm_manifest, etc. |
| Compat layout | `compat/legacy/layouts.py` | find_study_layout, find_reaction_json — v2 + legacy dual-probe resolution |
| NMR | `nmr/` | DP4/DP5, averaging, scaling, FCHL, spectra, report (see nmr/AGENTS.md) |
| Sampling projection | `results/sampling_graph.py` | build_sampling_energy_graph (view_type "sampling"); series energy-vs-time |
| ORCA parser | `results/orca_parser.py` | OrcaOutputParser → mode_frequencies/vectors/ir_intensities; zero modes kept |
| Frequencies | `results/frequencies.py` | build_normal_modes_product + build_frequency_report; corrupt modes skipped |
| Result manifest (read) | `results/manifest.py` | Unified `result_manifest.json` reader |
| Result manifest (write) | `storage/manifest.py` | Unified v2 writer (design doc §8) |
| TrajectoryFrame | `results/frames.py` | VIEW_REGISTRY (9 view_types) + to_node()/to_annotation() |
| Frame candidates | `results/frame_candidates.py` + `_store.py` + `_geometry.py` | Authority file `RESULT/frame_candidates.json` |
| Structure viewer | `results/structure_viewer.py` | Per-workflow resolvers + overlay (MCS + Kabsch RMSD) |
| IRC projection | `results/irc_projection.py` | Two-direction energy series in strict file order |
| Remote structure cache | `results/remote_structure_cache.py` | Flat cache + pending_fetch + sweep_expired; singleton owned by JobManager |
| API v1 | `api/v1_routes.py` | Jobs, files, detail, recovery matrix, edit-recalculate, PES review |
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
| Job manager | `scheduler/manager.py` | Lifecycle: pause/unpause/continue/rerun/purge + structure_cache singleton |
| Job runner | `scheduler/runner.py` | Background process execution; `python -m acp.cli run <workflow>` subprocess |
| Jobs model | `scheduler/jobs.py` | JobSpec, JobState, SUPPORTED_WORKFLOWS derived from active catalog |
| Store | `scheduler/store.py` | SQLite persistence, counts(), queue operations |
| Provenance | `scheduler/provenance.py` | Event sourcing, audit logging |
| Artifacts | `scheduler/artifacts.py` | Artifact tracking for stage workflows |
| Stage tasks | `scheduler/stage_tasks.py` | Plan providers mapping workflow → stage list |
| Local cleanup | `scheduler/local_cleanup.py` | Retention-based local disk cleanup |
| Migrations | `scheduler/migrations.py` | SQLite schema migrations (incl. 017 custom_name, 018 job_edit_operations) |
| Remote execution | `scheduler/remote/` | LSF bsub/bjobs, SSH/SFTP, code sync, result fetch (see remote/AGENTS.md) |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` — **新代码一律 PEP 604**
- **Docstrings**: compact single-line/短块，随所在包；cccp 侧 Google 风格
- **Backend design**: Capability Protocols (PEP 544)；backend 声明能力，不包含 subprocess 调用
- **No chem logic in core/**: core/ only generic mechanism（Structure, WorkflowRunner, Registry）
- **Stage pipeline**: WorkflowSpec 组装 Stage 函数；WorkflowRunner 顺序执行
- **Backend layering**: workflows 调 `get_backend(...)(...)`，不直接用 `cccp.qc.interfaces`
- **`__all__`**: 每个 `__init__.py` re-export public symbols
- **Job PAUSED 语义**: active 非终态（计入队列、禁删），poller 跳过；本地 killpg SIGSTOP/SIGCONT，远端 bstop/bresume；restart 本地 → FAILED `[RESTART_FAILED]`，远端保持 PAUSED
- **Task custom names**: `tasks.py` custom_name/name_revision；NOT in `_SYNC_COLUMNS`；`update_custom_name()` 事务性 revision check；PATCH `/api/v2/tasks/{id}` + expected_name_revision（409）
- **Structure-source org store**: `structure_source_store.py` — 4 tables；`source_uid_for()` = `ss_`+sha256；upsert 只碰 discovery fields；`RevisionConflictError`
- **Job-edit coverage**: `job_edit.py::EDIT_ACTIVE_WORKFLOWS` 必须同步；`audit_workflow_edit_coverage()` 守护

## ANTI-PATTERNS（acp 模块特有，全局规则见 root AGENTS.md）
- **Thin wrapper syndrome**: acp/io 和 acp/core/config 大量委托 cccp — 增加抽象但逻辑薄
- **Depends on cccp**: acp imports `cccp.config`, `cccp.io`, `cccp.qc.interfaces` — intentional coupling
- **`# pyright:` suppressions**: nmr/ 最密，pyright 不在工具链
- **Bare `except Exception:`**: api/v1_routes、chem/embedding、nmr/enumerate 等多处静默吞错
- **HARTREE_TO_KCAL duplication**: `acp/core/models.py` 和 `cccp/utils/constants.py` 各定义一次
- **Retired catalog entries retained**: ensemble/benchmark/energy 等保留 `status:"retired"` + `visible:False`；NMR 已于 P1a 重新激活（`status:"active"`）
