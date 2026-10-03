# Auto-Calc Platform (ACP) — Project Knowledge Base

**Generated:** 2026-09-30（slim v2 redo — 明细去重至嵌套 AGENTS.md；行数仅量级参考，以代码为准）
**Branch:** main

## OVERVIEW
Automated computational chemistry platform. Two packages under `src/`: `cccp`（Computational Chemistry Connection Package — QC 接口库，≈35 .py ≈14k 行）与 `acp`（统一模块，≈238 .py ≈103k 行，calculation-plan-driven 工作流 + 统一 CLI + API/scheduler/nmr）。Python 3.10+，setuptools，YAML config。

**可运行工作流（12，registry 驱动）**: `acp run Confsearch | PESsearch | BatchOptimize | irc | scan | tsmode | casscf | nmr | singlepoint | optimize | frequency | xtb_optimize`；simple 系列 + scan/irc 由 `workflows/simple.py` + `registry.py` 组装。另有 `acp run serve`（Web 服务）与 `acp doctor`（诊断）。

**Retired**（catalog `status:"retired"`，仅历史作业展示，**勿重建/勿扩展 CLI**）: ensemble, energy, xtbmd_censo_energy, mechanism, conformer, benchmark, mech-conf, mech-step, mech-confirm, mech-chain, optfreq, optfreqsp, Lowconfirm, Highconfirm。

**Job 生命周期**: QUEUED/RUNNING/PAUSED/WAITING_REVIEW/FAILED/CANCELLED/COMPLETED — PAUSED 见 CONVENTIONS；checkpoint continue / rerun（`{name}__rerun`）/ cascade purge。

## STRUCTURE（地图 — 叶级明细一律看嵌套 AGENTS.md）
```
src/cccp/                 # QC 接口库：config/software/version + core + qc/{interfaces,runners,cluster} + io + utils
src/acp/
├── cli.py                # argparse 统一入口：run/doctor/serve；retired 子命令拦截报错
├── catalog.py            # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS（≈4.3k 行；electronic-state/CASSCF 路由块）
├── confsearch/           # 4 协议构象搜索（xtb-crest/xtb-md/censo-crest/xtbmd-censo）+ sampling 帧/盆/饱和度
├── calculations/         # 计算基元唯一驻点：contracts/checkpoint/executor/plans + primitives/（sp/opt/freq/scan/irc/casscf/thermochemistry）+ pes/ + batch/ + irc/ + tsmode/
├── compat/legacy/        # 只读：历史 manifest 读取器 + 布局双探针
├── results/              # result_manifest 读取 + frames（TrajectoryFrame/VIEW_REGISTRY）+ sampling_graph + frame_candidates + structure_viewer + irc_projection + remote_structure_cache + orca_parser + frequencies
├── storage/              # result_manifest v2 写入（含 electronic-state product kinds）
├── core/  backends/  chem/  intake/  io/      # 通用机制 / 能力 Protocol 适配层 / 化学 / 摄入 / 结构 I/O
├── workflows/            # pes_search/batch_optimize/irc/simple/tsmode + registry（legacy 退役引擎仍作 Confsearch 协议引擎）
├── nmr/                  # DP4/DP5、平均、缩放/归属、FCHL、谱图、报告
├── api/                  # FastAPI：server/routes/v1_routes/v2_routes/v2_structure_sources/schemas + mechanism_readonly（历史只读）
└── scheduler/            # jobs/manager/runner/store/stage_tasks/tasks/task_views/molecule_groups/structure_source_store/structure_source_indexer/job_edit + remote/（LSF 远程执行）
frontend/                 # ACP_Workbench_v2.html（v1 遗留）；js/ 含 structure_viewer/structure_editor/vibration_viewer/structure_source_picker/task_input_workspace/candidate_details/job_editor
config/defaults.yaml      # 仅供参考 — Python built-in `_get_default_config()` 权威
tests/                    # ≈200 文件；conftest/fixtures/baseline（审计痕迹，勿提交 e2e-task-root.txt）
docs/                     # 权威设计文档（CENSO、Job File Layout、Simple、EVT Viewer、Electronic State CASSCF…）
requirements-node.txt     # 远端计算节点运行时依赖（numpy/rdkit/pyyaml）— 新增 `acp run` 运行时 import 需同步 HERE + pyproject.toml
pyproject.toml            # api/remote/nmr/dev extras；console script `acp = acp.cli:main`
```

## 嵌套知识库索引（动手前先读对应 AGENTS.md）
| 知识库 | 覆盖 |
|---|---|
| `src/acp/AGENTS.md` | acp 全模块叶级 WHERE TO LOOK（confsearch/pes/batch/irc/results/storage/core/backends/…） |
| `src/acp/api/AGENTS.md` | FastAPI 端点、SSE、schema |
| `src/acp/backends/AGENTS.md` | Protocol 适配层（ORCA/CREST/xTB/CENSO/Isostat/Molclus） |
| `src/acp/chem/AGENTS.md` | RDKit embedding + composition |
| `src/acp/core/AGENTS.md` | Structure/WorkflowRunner/Registry/State/Config |
| `src/acp/intake/AGENTS.md` | 文件解析器（6 格式） |
| `src/acp/nmr/AGENTS.md` | DP4/DP5 + FCHL + 谱图 + 报告 |
| `src/acp/scheduler/AGENTS.md` | Job 生命周期、持久化、stage_tasks |
| `src/acp/scheduler/remote/AGENTS.md` | LSF 远程执行：SSH/SFTP/bsub/bjobs/结果拉取 |
| `src/acp/workflows/AGENTS.md` | 工作流实现 + registry |
| `src/cccp/AGENTS.md` | cccp 包根：config/version + 5 子包 |
| `src/cccp/core/AGENTS.md` | ConformerEngine（dormant）+ ProtocolSpec + CandidateSet |
| `src/cccp/io/AGENTS.md` | MolecularInputHandler（格式检测 + RDKit embedding） |
| `src/cccp/pipeline/AGENTS.md` | PipelineExecutor（thin） |
| `src/cccp/qc/AGENTS.md` | QC 子进程层总览（interfaces/runners/cluster） |
| `src/cccp/qc/cluster/AGENTS.md` | Local + LSF 适配器（factory in `__init__`） |
| `src/cccp/qc/interfaces/AGENTS.md` | ORCA/CREST/xTB/CENSO/ISOSTAT/Molclus 子进程封装 |
| `src/cccp/utils/AGENTS.md` | File I/O、几何、常数、溶剂映射 |
| `tests/AGENTS.md` | 测试约定、fixture 分层、慢测门控 |
| **本文件（root）** | 全局契约、ANTI-PATTERNS、COMMANDS |

## WHERE TO LOOK（跨包入口 + 全局契约；模块内细节先查嵌套表）
- **CLI/工作流注册**: `src/acp/cli.py` + `workflows/registry.py`；退役拦截 `_CLI_REMOVED_WORKFLOWS`
- **新增 QC 协议**: `src/cccp/core/protocols.py::_get_default_protocol_config()`（YAML protocols 节不可达）
- **配置**: `src/cccp/config.py`（6 源合并）；ACP 侧 `src/acp/core/config.py`；**数据目录唯一权威 `src/acp/core/paths.py::resolve_run_root`**
- **计算基元**: `calculations/primitives/`（electronic-state 门控在 `_common.py`；`casscf.py` CASSCF/NEVPT2）；单步 `executor.py::CalculationPlanExecutor`；多步 `batch/engine.py`
- **结果契约**: 写 `storage/manifest.py`（v2）→ 读 `results/manifest.py`；帧契约 `results/frames.py::VIEW_REGISTRY` + `to_node()/to_annotation()`；帧候选权威 `frame_candidates.json`
- **调度**: `scheduler/manager.py`（pause/unpause/continue/rerun/purge/resume）；`structure_sources.py`（COMPLETED 任务结构来源）；`job_edit.py`（修改参数后重算 + edit-coverage registry）
- **API**: `api/v1_routes.py`（任务/文件/detail/recovery 矩阵/edit-recalculate）+ `api/v2_routes.py` + `api/v2_structure_sources.py`（结构来源组织端点）
- **NMR**: 全套见 `src/acp/nmr/AGENTS.md`；工作流入口 `workflows/nmr.py`
- **Frontend**: `frontend/ACP_Workbench_v2.html` — 能量/轨迹查看器、任务向导、结构来源面板、job_editor
- **权威设计文档**: `docs/ACP_Job_File_Layout_Spec.md`（文件布局契约）、`docs/ACP_CENSO_Integration_DevDoc.html`（CENSO 审计）、`docs/ACP_Energy_Trajectory_Viewer_DevDoc.md`（帧契约/采样管线）、`docs/ACP_PES_Manual_Review_DevDoc.md`（PES 人工选点 → BatchOptimize）、`docs/ACP_Simple_Workflows_DevDoc.html`（simple 工作流）

## CONVENTIONS
- **Docstrings**: cccp Google 风格（`Args:/Returns:`）；acp 紧凑单行/短块 — 随所在包
- **注解**: 旧 cccp 用 `typing.Optional[X]`；acp 与新增 cccp 用 PEP 604 `X | None` + `from __future__ import annotations` — **新代码一律 PEP 604**
- **Imports**: stdlib → 第三方（numpy/rdkit）→ local；每模块 `logger = logging.getLogger(__name__)`；`pathlib.Path` + `os.replace` 原子写（禁 `os.path.*`）
- **Dataclasses**: `@dataclass(frozen=True)` 用于 spec/contracts；可变仅数据容器
- **抽象**: cccp `ABC` base vs acp 结构 `Protocol`（PEP 544）；例外 `QCBackend(ABC)`、`ErrorModel(ABC)`、`CRESTInterface` 无基类（勿"修正"）；`XTBInterface` 独立于 `qc/interfaces/xtb.py`（勿回并 `crest.py`）
- **风格**: `__all__` 全量 re-export；旧 cccp 模块 docstring 带 `====` 下划线 + `Author: QCcalc Team`
- **工具链**: ruff（E/F/I/N/W/UP）+ ruff-format + mypy(strict) + pre-commit；CI 见 ANTI §10
- **Job PAUSED 语义**: active 非终态（计入队列、禁删），poller 跳过；本地 killpg SIGSTOP/SIGCONT（进程留守 `_processes`，不释放内存/磁盘），远端 LSF `bstop/bresume`；restart：本地冻结 → FAILED `[RESTART_FAILED]`，远端重收养保持 PAUSED；cancel 顺序 SIGCONT → SIGTERM → SIGKILL
- **Task custom names**: `tasks.py` custom_name/name_revision/name_updated_at（NOT in `_SYNC_COLUMNS` — sync never overwrites）；`update_custom_name()` 事务性 revision check + organization_events 审计；PATCH `/api/v2/tasks/{id}` + expected_name_revision（409 carries current projection）
- **Structure-source org store**: `structure_source_store.py` — 4 tables；`source_uid_for()` = `ss_`+sha256(job_id+relpath)[:24]；upsert 只碰 discovery fields；`RevisionConflictError` + expected_revision 乐观锁
- **Job-edit coverage registry**: `job_edit.py::EDIT_ACTIVE_WORKFLOWS` — 新 active 工作流必须登记，`audit_workflow_edit_coverage()` + `tests/test_acp_job_edit.py` 守护

## MIGRATION PERIOD RULES（acp→cccp 架构整改迁移期规则；todo 32 收口并入 #17）
> 仅迁移期有效（基线 `main@2a23b93`）；最终合并进 ANTI-PATTERNS #17 在 todo 32。台账行格式见 `tests/baseline/refactor-evidence/migration_ledger.md`：**能力/当前实现驻点/兼容入口/生产消费者/退出条件/验收测试**。
- **新驻点（new station）**: QC 执行任务实现唯一落点 `src/cccp/calculation/`（cccp.calculation 任务层）；`src/cccp/**` 禁止导入 `acp`（含懒加载/TYPE_CHECKING）
- **过渡期双驻点（transitional dual-station）**: `src/acp/calculations/primitives/` 迁移期为兼容 shim（纯转发/重导出），实现体在 `cccp.calculation` — 两根合并每基元**恰好一个实现体**；守护 `tests/test_architecture_invariants.py::test_unique_primitive_definitions`（cccp 根 Wave 0 不存在=空；todo 16/23 前勿硬指 `cccp/calculation/tasks`）
- **唯一实现当前驻点台账（current-station ledger）**: 每能力一行记录实现体现驻点，实现体移动即改行，退出条件满足才删行；新能力（含 P2 任务）先入台账再落码
- **迁移期护栏适配**: `tests/test_f4_scope_audit.py` 基线=2a23b93（QC 接口层新改动须登记 amendment）；`scripts/check_grep_gates.py` 四个冻结 pin（`unique_run_scan`/`unique_run_irc`/`wave2_shermo_external`/`final_shermo`）为待迁移重定向清单 — 代码移动的同一 todo 重定向门，禁禁用/静默放宽；跨版本恢复 fixtures 固定于 `tests/baseline/recovery_fixtures/`（可复现生成脚本同目录）

## ANTI-PATTERNS（硬性铁律）
1. **NEVER `pymatgen`** — 已从 pyproject 移除（零引用）
2. **NEVER 只更新一处 `__version__`** — 4 处同步：`cccp/__init__.py`、`cccp/version.py`、`acp/__init__.py`、`pyproject.toml`
3. **YAML defaults 与 Python built-in 须同步** — `config/defaults.yaml` 与 `_get_default_config()`；built-in 权威
4. **协议只进 `_get_default_protocol_config()`** — YAML `protocols` 节不可达
5. **NEVER 在 `__init__.py` 放实现** — 例外仅 `qc/cluster/__init__`（factory）与 `qc/runners/__init__`（整模块）
6. **`__main__` 仅 acp** — `python -m acp`/`python -m acp.cli` 可用，`python -m cccp` 不可用；旧 `conformer-search` console_script 已删，一律 `acp`
7. **ORCA `%geom` 关键字是 `Trust` 非 `TrustRadius`** — TrustRadius 被输入解析拒（ORCA 5/6.x）；`MaxIter` 出自 `max_cycles`/`geom_maxiter`
8. **新代码禁裸 `except Exception:`** — 历史 80+ 处蔓延，pyright suppressions 重（nmr/ 最密，pyright 不在工具链）
9. **常量单点定义** — `HARTREE_TO_KCAL` ×2（值同）、气体常数 R ×3（精度不一）；新常量禁复制
10. **CI（2026-08-21 起）**: push main/feat/** + PR → py3.10/3.11/3.12 compileall + `pytest -m "not slow"`。install 行必须含全部 extras — `pip install -e '.[dev,api,remote]'`（漏 `api` 是 2026-09-05 全红原因）。lint/format 门仍注释中
11. **`_SCHEDULER_MARKERS` 必须列出调度器预建文件全集** — 漏列使 `_resolve_output_dir` 把产物重定向到不可见 `<work_dir>_1/`
12. **新增 job status 必须全表面同步** — `jobs.py::JobStatus` is_active/is_terminal + `store.counts()` 消费者 + 前端 `getStatusClass`/i18n（zh+en）
13. **NEVER 裸 `DELETE FROM jobs`** — schema 无 FK 级联；走 `store.purge_cascade`（先删 tasks 行）
14. **`resume()` 仅 WAITING_REVIEW review-only** — pause/unpause/continue 是独立方法；非 requeue resume 有 RUNNING-bounce footgun
15. **NEVER 把 `job_id`/时间戳放进磁盘路径** — 任务目录 `<molecule>_<task>_<remark>`（`JobSpec.task_dir_name()`），job_id 仅 DB PK/job.json/task.json/WORK/00_RUNTIME/events.jsonl；调度上下文扁平写（`is_scheduler_task_dir`），非调度 CLI 保留 `{output}/{safe_name}/`
16. **mechanism 布局归一化（v1.2）** — 新任务产物落 `WORK/{02_SEARCH,03_OPT/TS,07_PATH,08_ANALYSIS}` + `RESULT/mechanism`；**禁直接拼 `mechanism_study/<id>/` 路径**
17. **calculations/ 是计算基元唯一驻点** — 新计算能力进 `calculations/primitives/`；single-item 单步走 `CalculationsPlanExecutor`，多步走 BatchOptimizeEngine
18. **compat/ 只读禁止写入** — 新代码消费数据走 `results/manifest`（v2），仅历史任务经 compat 转接
19. **两层目录: 安装目录 ≠ 数据目录** — run_root 须原生文件系统（9p/nfs/cifs 慢 ~1000×）；禁 `./ACP_runs` 相对默认、禁默认 `/tmp` work_root；迁移 `python scripts/migrate_run_root.py <old> <new>`
20. **前端禁硬编码退役 id 作默认值** — 向导默认经 `resolveDefaultWorkflow()`/`pickDefaultProfile()` 目录驱动，消费点先 `ensureWizardWorkflowValid()`；退役字面量仅许历史兼容分支
21. **NEVER 绕过 `acp.results.frames` 契约发视图投影** — 一律经 `TrajectoryFrame.to_node()`/`TrajectoryAnnotation.to_annotation()`；新视图类型先注册 `VIEW_REGISTRY::ViewSpec`
22. **energy viewer 禁提交任务** — 帧操作仅"保存为候选"；`energyGraphConfirmAndBatch` 为前端禁止标识符；3Dmol viewer 必须经 `scheduleViewerFraming`/`frame3DViewer`
23. **NEVER 添加 active 工作流而不登记 edit coverage** — `job_edit.py::EDIT_ACTIVE_WORKFLOWS` 必须同步；`audit_workflow_edit_coverage()` 守护
24. **远程提交必须带 scheduler markers** — `job.json` + `task.json` 缺失致远程嵌套错误；`_upload_scheduler_markers` 在 bsub 前调用
25. **keyword choices 大小写折叠只走三个 choke points** — CLI parser `_normalize_string_choices`、catalog `normalize_and_validate_method_config`、BatchOptimize `_canonicalize_batch_keywords`；禁逐参数 `type=` 包装器
26. **远程 `pending_fetch` 禁仅依赖浏览器 `?fetch=1`** — 后台 terminal transition 时 `_queue_catalog_prefetch`；前端 `?fetch=1` 为快速路径非唯一路径
27. **远程作业结果读取必须经 `_job_read_root`** — 禁直接读 `record.work_dir`；远端走 `RemoteStructureCache.fetch_catalog`；写回走 `push_paths`
28. **前端 `api()` 超时禁泄露 raw AbortError** — SFTP 端点用 `apiRemote()`（60s）；默认 8s 只用于本地端点

## COMMANDS
```bash
# Install
pip install -e .                 # 运行全部 pytest 需 -e '.[dev,api,remote]'（CI 同）
pip install -e '.[api]' / '.[remote]' / '.[dev]'

# Run — 12 个可运行工作流（retired 子命令会被 CLI 拦截并提示替代）
acp run Confsearch --input "CCO" --protocol xtb-crest --refinement-policy screen --output ./out
acp run Confsearch --input "CCO" --protocol censo-crest --profile light
acp run PESsearch --from-job 20260823_001_Confsearch --output ./pes_out
acp run BatchOptimize --from-job 20260823_002_PESsearch --output ./batch_out
acp run BatchOptimize --items-file structures.xyz --profile opt_freq_sp_thermo
acp run irc --input ts_structure.xyz --output ./irc_out
acp run scan --input "CCO" --coordinate 3,4,1.0,3.0 --output ./scan_out
acp run nmr --input "CCO" --backend orca --reference "13C=185.0" "1H=31.5"
acp run singlepoint --input "CCO" --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c" --charge 0 --multiplicity 1
acp run frequency --input molecule.xyz
acp run xtb-optimize --input molecule.xyz
acp run tsmode --source-bundle bundle.json --source-mode-index 0
acp run casscf --input molecule.xyz
acp run serve --port 8765
acp doctor

# Retired → 现役替代
# ensemble→Confsearch censo-crest screen · energy→Confsearch rank1/cumulative-99 · xtbmd_censo_energy→Confsearch xtbmd-censo
# mechanism→PESsearch → BatchOptimize → irc · optfreq/optfreqsp/Lowconfirm/Highconfirm→BatchOptimize 各 profile

# Test
pytest tests/ -v && pytest tests/ --run-slow -v && pytest -m "not slow" -v
pytest tests/test_frontend_sync.py tests/test_acp_energy_graph.py -v
pytest tests/test_acp_workflows_nmr.py tests/test_acp_backends.py -v
pytest tests/test_remote_phase1.py -v

# Lint/format（pre-commit hooks = ruff --fix + ruff-format）
ruff check src tests && ruff format --check src tests
```

## SYSTEMD SERVICE
- **Service**: `acp.service` · config `/etc/systemd/system/acp.service`（**generated — 编辑 `scripts/install_systemd.sh`，勿手改 unit**）· URL http://127.0.0.1:8765 · 日志 `sudo journalctl -u acp -f`
- **Reload**: 任何代码修改后 `sudo systemctl restart acp`（服务无 `--reload`）
