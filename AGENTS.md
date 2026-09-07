# Auto-Calc Platform (ACP) — Project Knowledge Base

**Generated:** 2026-09-07（精简 v2 — 明细去重至嵌套 AGENTS.md；行数仅量级参考，以代码为准）
**Branch:** main

## OVERVIEW
Automated computational chemistry platform. Two packages under `src/`: `cccp`（Computational Chemistry Connection Package — QC 接口库，34 .py ≈12.7k 行）与 `acp`（统一模块，199 .py ≈74.5k 行，calculation-plan-driven 工作流 + 统一 CLI + API/scheduler/nmr）。Python 3.10+，setuptools，YAML config。

**可运行工作流（11，registry 驱动）**: `acp run Confsearch | PESsearch | BatchOptimize | irc | scan | nmr | singlepoint | optimize | frequency | casscf | xtb-optimize`；simple 系列（singlepoint/optimize/frequency/casscf/xtb-optimize）+ scan/irc 由 `workflows/simple.py` + `registry.py` 组装。另有 `acp run serve`（Web 服务）与顶层 `acp doctor`（诊断）。simple 系列支持 electronic-state 接线（`--charge/--multiplicity/--spin-preset/--spin-config`，2026-09 CASSCF 波次，见 docs/ACP_Electronic_State_CASSCF_Design.md）。

**Retired**（catalog `status:"retired"`，仅历史作业展示，**勿重建/勿扩展 CLI**）: ensemble, energy, xtbmd_censo_energy, mechanism, conformer, benchmark, mech-conf, mech-step, mech-confirm, mech-chain, optfreq, optfreqsp, Lowconfirm, Highconfirm。

**Job 生命周期**: QUEUED/RUNNING/PAUSED/WAITING_REVIEW/FAILED/CANCELLED/COMPLETED — PAUSED 见 CONVENTIONS；checkpoint continue / rerun（`{name}__rerun`）/ cascade purge 见 scheduler/AGENTS.md 与 docs/ACP_Job_File_Layout_Spec.md。

## STRUCTURE（地图 — 叶级明细一律看嵌套 AGENTS.md）
```
src/cccp/                 # QC 接口库：config/software/version + core + qc/{interfaces,runners,cluster} + io + utils
src/acp/
├── cli.py                # argparse 统一入口（≈2.9k 行）：run/doctor/serve；retired 子命令拦截报错
├── catalog.py            # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS（≈4.3k 行；electronic-state/CASSCF 路由块）
├── confsearch/           # 4 协议构象搜索（xtb-crest/xtb-md/censo-crest/xtbmd-censo）+ sampling 帧/盆/饱和度
├── calculations/         # 计算基元唯一驻点：contracts/checkpoint/executor/plans + primitives/（sp/opt/freq/scan/irc/casscf/thermochemistry）+ pes/ + batch/ + irc/
├── compat/legacy/        # 只读：历史 manifest 读取器 + 布局双探针
├── results/              # result_manifest 读取 + frames（TrajectoryFrame/VIEW_REGISTRY）+ sampling_graph + frame_candidates 服务
├── storage/              # result_manifest v2 写入（含 electronic-state product kinds）
├── core/  backends/  chem/  intake/  io/      # 通用机制 / 能力 Protocol 适配层 / 化学 / 摄入 / 结构 I/O
├── workflows/            # pes_search/batch_optimize/irc/simple + registry（legacy 退役引擎仍作 Confsearch 协议引擎）
├── nmr/                  # DP4/DP5、平均、缩放/归属、FCHL、谱图、报告
├── api/                  # FastAPI：server/routes/v1_routes/v2_routes/schemas + mechanism_readonly（历史只读）
└── scheduler/            # jobs/manager/runner/store/stage_tasks/structure_sources/... + remote/（LSF 远程执行）
frontend/                 # ACP_Workbench_v2.html（v1 遗留）；向导默认值目录驱动（见 ANTI §21）
config/defaults.yaml      # 仅供参考 — Python built-in `_get_default_config()` 权威
tests/                    # 130 文件 ≈2181 测试函数；conftest/fixtures/baseline（审计痕迹，勿提交 e2e-task-root.txt）
docs/                     # 权威设计文档（CENSO、Job File Layout、Simple、EVT Viewer、Electronic State CASSCF…）
requirements-node.txt     # 远端计算节点运行时依赖（numpy/rdkit/pyyaml）— 新增 `acp run` 运行时 import 需同步 HERE + pyproject.toml
pyproject.toml            # api/remote/nmr/dev extras；console script `acp = acp.cli:main`
```

## 嵌套知识库索引（动手前先读对应 AGENTS.md）
| 知识库 | 覆盖 |
|---|---|
| `src/acp/AGENTS.md` | acp 全模块叶级 WHERE TO LOOK（confsearch/pes/batch/irc/results/storage/core/backends/…） |
| `src/acp/api/AGENTS.md` · `scheduler/AGENTS.md` · `scheduler/remote/AGENTS.md` · `workflows/AGENTS.md` · `nmr/AGENTS.md` | 各自领域细目 |
| `src/cccp/AGENTS.md`（+ cccp/core、qc、io、utils 等子目录） | 底层 QC 库细目 |
| `tests/AGENTS.md` | 测试约定 |

## WHERE TO LOOK（跨包入口 + 全局契约；模块内细节先查嵌套表）
- **CLI/工作流注册**: `src/acp/cli.py` + `workflows/registry.py`（11 个 runnable keys + fake 测试项）；退役拦截 `_CLI_REMOVED_WORKFLOWS`
- **新增 QC 协议**: `src/cccp/core/protocols.py::_get_default_protocol_config()`（YAML protocols 节不可达）
- **配置**: `src/cccp/config.py`（6 源合并，读 ~/.cccp.yaml 回退 ~/.conformer_search.yaml）；ACP 侧 `src/acp/core/config.py`；**数据目录唯一权威 `src/acp/core/paths.py::resolve_run_root`**
- **计算基元**: `calculations/primitives/`（electronic-state 解析+质量门在 `_common.py`；`casscf.py` CASSCF/NEVPT2 原语）；单步调度 `executor.py::CalculationPlanExecutor`；多步批量 `calculations/batch/engine.py`（2026-09 起 item×state 展开、缓存键分离、gbw 继承）
- **结果契约**: 写 `storage/manifest.py`（v2）→ 读 `results/manifest.py`；帧契约 `results/frames.py::VIEW_REGISTRY`（9 view_types）+ `to_node()/to_annotation()`；采样投影 `sampling_graph.py`；帧候选权威文件 `frame_candidates.json`（`frame_candidates.py`/`_store.py`/`_geometry.py`，含路径逃逸守卫）
- **调度**: `scheduler/manager.py`（pause/unpause/continue/rerun/purge/resume 语义见 ANTI §15）；`runner.py`（`python -m acp.cli run <wf>` 子进程）；`structure_sources.py`（COMPLETED 任务结构来源，TAG: TS/INT + candidate_id）；任务目录命名见 Job File Layout Spec
- **API**: `api/v1_routes.py` 任务/分子/文件 + `GET /jobs/{id}/detail`（recovery 矩阵）+ queue 操作端点；`api/v2_routes.py`；历史 mechanism 作业只读投影 `api/mechanism_readonly*.py`（勿扩展）
- **方法/目录元数据**: `catalog.py` METHOD_META/METHOD_SCHEMAS（active+retired 全量）
- **NMR**: 全套见 `src/acp/nmr/AGENTS.md`；工作流入口 `workflows/nmr.py`
- **Frontend**: `frontend/ACP_Workbench_v2.html`（v1 仅历史）— 能量/轨迹查看器、任务向导、结构来源面板

## 权威设计文档（docs/）
| 文档 | 用途 |
|---|---|
| `ACP_Job_File_Layout_Spec.md` | job/work_dir 文件布局契约（任务目录命名 §1a/2a/6a，调度扁平写根） |
| `ACP_CENSO_Integration_DevDoc.html` | CENSO 集成权威设计 + P1–P5 审计（v14 验收通过） |
| `ACP_Electronic_State_CASSCF_Design.md` | 2026-09 electronic-state/CASSCF 能力设计 |
| `ACP_Energy_Trajectory_Viewer_DevDoc.md` + `ACP_PES_Manual_Review_DevDoc.md` | TrajectoryFrame 契约/采样管线/帧候选；PES 人工选点 → BatchOptimize 链路 |
| `ACP_Simple_Workflows_DevDoc.html` · `ACP_NMR_DP4_DevDoc.md` | simple 工作流 / NMR DP4 设计 |
| `ACP_Mechanism_Research_DevDoc.md` | **RETIRED** mechanism 设计，仅参考（勿按此开发） |

## CONVENTIONS
- **Docstrings**: cccp Google 风格（`Args:/Returns:`）；acp 紧凑单行/短块 — 随所在包
- **注解**: 旧 cccp 用 `typing.Optional[X]`；acp 与新增 cccp 用 PEP 604 `X | None` + `from __future__ import annotations` — **新代码一律 PEP 604**
- **Imports**: stdlib → 第三方（numpy/rdkit）→ local；每模块 `logger = logging.getLogger(__name__)`；`pathlib.Path` + `os.replace` 原子写（禁 `os.path.*`）
- **Dataclasses**: `@dataclass(frozen=True)` 用于 spec/contracts；可变仅数据容器
- **抽象**: cccp `ABC` base vs acp 结构 `Protocol`（PEP 544）；例外 `QCBackend(ABC)`（backends/base.py）、`ErrorModel(ABC)`（nmr/error_model.py）、`CRESTInterface` 无基类（勿"修正"）
- **风格**: `__all__` 全量 re-export；旧 cccp 模块 docstring 带 `====` 下划线 + `Author: QCcalc Team`
- **工具链**: ruff（E/F/I/N/W/UP）+ ruff-format + mypy(strict) + pre-commit；CI 见 ANTI §11
- **Job PAUSED 语义**: active 非终态（计入队列、禁删），poller 跳过；本地 killpg SIGSTOP/SIGCONT（进程留守 `_processes`，不释放内存/磁盘），远端 LSF `bstop/bresume`；restart：本地冻结 → FAILED `[RESTART_FAILED] paused job frozen at restart — 可续算(continue)`，远端重收养保持 PAUSED；cancel 顺序 SIGCONT → SIGTERM → SIGKILL（冻结进程忽略 SIGTERM）

## ANTI-PATTERNS（硬性铁律）
1. **NEVER `pymatgen`** — 已从 pyproject 移除（验证零引用）
2. **NEVER 只更新一处 `__version__`** — 4 处同步：`cccp/__init__.py`、`cccp/version.py`、`acp/__init__.py`、`pyproject.toml`
3. **NEVER 只改 YAML 默认值** — `config/defaults.yaml` 与 Python built-in `_get_default_config()` 须同步（built-in 权威）
4. **协议只进 `_get_default_protocol_config()`** — YAML `protocols` 节不可达
5. **NEVER 在 `__init__.py` 放实现** — 现存例外仅 `qc/cluster/__init__.py`（factory）与 `qc/runners/__init__.py`（整模块）
6. **`__main__` 仅 acp** — `python -m acp`/`python -m acp.cli` 可用，`python -m cccp` 不可用；`conformer-search` console_script 已删，一律 `acp`
7. **ORCA `%geom` 关键字是 `Trust` 非 `TrustRadius`** — TrustRadius 在输入解析即拒（ORCA 5/6.x）；`MaxIter` 出自 `max_cycles`/`geom_maxiter`（`orca_ts.ts_geom_block`）
8. **`# pyright:` suppressions 重（nmr/ 最密）** — pyright 不在工具链，新代码勿新增
9. **NEVER 裸 `except Exception:`** — 历史 80+ 处蔓延（v1_routes/chem.embedding/nmr.enumerate 最重），新代码一律精确捕获
10. **常量单点定义** — 历史重复：`HARTREE_TO_KCAL` ×2（值同 627.5094740631）、气体常数 R ×3（精度不一！）；新常量禁复制
11. **CI（2026-08-21 起）**: push main/feat/** + PR → py3.10/3.11/3.12 compileall + `pytest -m "not slow"`。**install 行必须含套件模块级 import 的全部 extras** — `pip install -e '.[dev,api,remote]'`（漏 `api` 是 2026-09-05 全红原因）。lint/format 门仍注释中（~560 ruff 待清扫）
12. **`simple.py::_SCHEDULER_MARKERS` 必须列出调度器预建文件全集** — 漏列使 `_resolve_output_dir` 把产物重定向到不可见 `<work_dir>_1/`（含 `mechanism_config.json` 等历史键；v1.2 已除小写 scaffolding 键）
13. **新增 job status 必须全表面同步** — `jobs.py::JobStatus` is_active/is_terminal + `store.counts()` 消费者（schemas::QueueCounts + routes）+ 前端 `getStatusClass`/`getQueueCounts`/`isActiveJobStatus` + i18n（zh+en）
14. **NEVER 裸 `DELETE FROM jobs`** — schema 无 FK 级联，删任务走 `store.purge_cascade`（manager._purge_job_records），否则孤儿 decision_points/artifacts/stage_tasks
15. **`resume(job_id, resolution)` 仅 WAITING_REVIEW review-only** — pause/unpause/continue 是独立方法；非 requeue resume 有 RUNNING-bounce footgun（无线程置 RUNNING → exit_code 77 持久 → 弹回 WAITING_REVIEW）
16. **NEVER 把 `job_id`/时间戳放进磁盘路径** — 任务目录 `<molecule>_<task>_<remark>`（`JobSpec.task_dir_name()`），重名 `__NN`（`_dedupe_task_dir`）；job_id 仅 DB PK/job.json/task.json/WORK/00_RUNTIME/events.jsonl；调度上下文任务根扁平写入（`_helpers.is_scheduler_task_dir`），非调度 CLI 保留 `{output}/{safe_name}/`
17. **calculations/ 是计算基元唯一驻点** — 新计算能力必须进 `calculations/primitives/`，不得散落 workflows/backends；single-item 单步调度唯一入口 `CalculationsPlanExecutor`，多项目走 BatchOptimizeEngine
18. **compat/ 只读禁止写入** — 新代码消费数据走 `results/manifest`（v2），仅历史任务经 compat 转接
19. **两层目录（2026-08-30）: 安装目录 ≠ 数据目录** — run_root 须原生文件系统（9p/nfs/cifs 慢 ~1000×，实测）；`resolve_run_root` 唯一权威：`--run-root` > `ACP_RUN_ROOT` > 平台默认（root → /var/lib/acp/runs，用户 → XDG）；禁 `./ACP_runs` 相对默认；QC work 禁默认 /tmp（曾致不可见产物 + 远端泄漏）；迁移 `python scripts/migrate_run_root.py <old> <new>`（旧树只读归档）；哨兵 `check_run_root_safety` 只警告，`ACP_ALLOW_SLOW_FS=1` 豁免
20. **mechanism 已删除（2f7b4e3 wave-8）** — 历史作业只读展示经 `api/mechanism_readonly*.py`（410 语义）+ compat/legacy 读取器；**禁重建机制代码、禁拼 `mechanism_study/<id>/` 路径**（grep 校验零残留）
21. **前端禁硬编码退役工作流/schema/profile id 作默认值（2026-09-07）** — 向导默认经 `resolveDefaultWorkflow()`/`pickDefaultProfile()` 目录驱动，消费点先 `ensureWizardWorkflowValid()` 自愈；退役字面量仅许历史兼容分支；`tests/test_frontend_sync.py` 动态读 catalog 拦截回归
22. **NEVER 绕过 `acp.results.frames` 契约发视图投影** — 一律经 `TrajectoryFrame.to_node()`/`TrajectoryAnnotation.to_annotation()`；新视图类型先注册 `VIEW_REGISTRY::ViewSpec`，否则 wire 形状不一致 + 前端渲染失败
23. **energy viewer 禁提交任务** — 帧操作仅"保存为候选"（物化 XYZ + 注册 manifest）；`energyGraphConfirmAndBatch` 与 `data-energy-action="to-batch"` 为前端禁止标识符（tests/test_frontend_sync.py 锁定）；任务创建统一走"新建任务" + 结构来源面板

## COMMANDS
```bash
# Install
pip install -e .                 # 运行全部 pytest 需 -e '.[dev,api,remote]'（CI 同）
pip install -e '.[api]' / '.[remote]' / '.[dev]'   # FastAPI+uvicorn / paramiko / pytest+ruff+mypy

# Run — 11 个可运行工作流（retired 子命令会被 CLI 拦截并提示替代）
acp run Confsearch --input "CCO" --protocol xtb-crest --refinement-policy screen --output ./out
acp run Confsearch --input "CCO" --protocol censo-crest --profile light
acp run Confsearch --input "CCO" --protocol xtbmd-censo --refinement-policy rank1
acp run Confsearch --input "CCO" --protocol xtb-md --refinement-policy cumulative-99
acp run PESsearch --from-job 20260823_001_Confsearch --output ./pes_out
acp run BatchOptimize --from-job 20260823_002_PESsearch --output ./batch_out
acp run BatchOptimize --items-file structures.xyz --profile opt_freq_sp_thermo
acp run irc --input ts_structure.xyz --output ./irc_out
acp run scan --input "CCO" --coordinate 3,4,1.0,3.0 --output ./scan_out
acp run nmr --input "CCO" --backend orca --reference "13C=185.0" "1H=31.5"
acp run singlepoint --input "CCO" --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c" --charge 0 --multiplicity 1
acp run casscf --input molecule.xyz            # CASSCF/NEVPT2（electronic-state 波次）
acp run serve --port 8765
acp doctor                                    # 顶层诊断

# Retired 入口 → 现役替代
# ensemble→Confsearch censo-crest screen · energy→Confsearch rank1/cumulative-99 · xtbmd_censo_energy→Confsearch xtbmd-censo
# mechanism→PESsearch → BatchOptimize(--profile opt_freq|opt_freq_sp_thermo) → irc · optfreq/optfreqsp/Lowconfirm/Highconfirm→BatchOptimize 各 profile

# Test（真实二进制测试 collection 跳过；--run-slow 纳入）
pytest tests/ -v && pytest tests/ --run-slow -v && pytest -m "not slow" -v
pytest tests/test_frontend_sync.py tests/test_acp_energy_graph.py -v     # 前端契约 + 能量图
pytest tests/test_acp_workflows_energy.py tests/test_acp_workflows_nmr.py -v
pytest tests/test_acp_backends.py tests/test_acp_censo_p5_acceptance.py -v
pytest tests/test_remote_phase1.py tests/test_acp_api_mechanism_*.py -v

# Lint/format（pre-commit hooks = ruff --fix + ruff-format）
ruff check src tests && ruff format --check src tests
```

## SYSTEMD SERVICE
- **Service**: `acp.service` · config `/etc/systemd/system/acp.service`（**generated — 编辑 `scripts/install_systemd.sh`，勿手改 unit**）· URL http://127.0.0.1:8765 · 日志 `sudo journalctl -u acp -f`
- **Reload**: 任何代码修改后 `sudo systemctl restart acp`（服务无 `--reload`）
