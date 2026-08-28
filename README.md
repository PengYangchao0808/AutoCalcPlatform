# ACP — 自动化化学计算平台

**Auto-Calc Platform (ACP)** 是一个模块化的 Python 计算化学平台，旨在降低量子化学计算的入门门槛，通过 CLI 和 Web 界面让不熟悉计算化学流程的研究人员也能轻松提交任务、查看进度、可视化结果。

---

## 开发状态

| 阶段 | 状态 | 内容 |
|------|------|------|
| **Phase 0** | ✅ 完成 | 底层 `cccp`（Computational Chemistry Connection Package）QC 接口库 |
| **Phase 1** | ✅ 完成 | 模块化重构 + `acp` 统一模块 + Confsearch/PESsearch/BatchOptimize/irc/scan 计算工作流 + nmr/simple 工作流 + CENSO 集成 |
| **Phase 2** | ✅ 完成 | FastAPI Web 后端 + 任务调度器 + 远程 LSF 执行 |
| **Phase 4** | ✅ 完成 | 极简化重构：mechanism/ 删除，计算基元上浮至 calculations/，BatchOptimize/irc/scan 独立工作流 |

> 注：conformer / benchmark / ensemble / energy / xtbmd_censo_energy / mechanism / mech-conf / mech-step / mech-confirm / mech-chain / optfreq / optfreqsp / Lowconfirm / Highconfirm 工作流已于 2026-08-28 退役（catalog 中保留为 `status:"retired"` 仅用于历史作业展示）。构象搜索能力统一由 `acp run Confsearch --protocol <4种协议>` 提供；低精度确认由 `acp run BatchOptimize --profile opt_freq` 提供；高精度确认由 `acp run BatchOptimize --profile opt_freq_sp_thermo` 提供。

---

## 功能概览

### 1. Confsearch — 统一构象搜索 + 能量排名 `acp run Confsearch`
- SMILES / XYZ / GJF / LOG / OUT / SDF / MOL / ORCA INP 多格式输入
- 四种协议：`--protocol xtb-crest`（CREST搜索+DFT精修）、`xtb-md`（xTB-MD采样+DFT）、`censo-crest`（CREST+CENSO排序）、`xtbmd-censo`（xTB-MD+ISOSTAT+CENSO）
- 资源档位：`--profile light|default|high`
- 精修策略：`--refinement-policy screen|rank1|cumulative-99|all`
- 统一产物：`confsearch_manifest.json` 供下游 PESsearch 消费

### 2. PESsearch — 势能面搜索 `acp run PESsearch`
- 从 Confsearch manifest 出发，搜索反应路径（PEB引导扫描 / 直接TS猜测）
- 输出：`RESULT/pes_search/pes_profile.json` + TS/中间体候选结构
- 输入方式：`--from-job <Confsearch job id>` 或 `--from-artifact RESULT/confsearch/confsearch_manifest.json`

### 3. BatchOptimize — 批量优化确认 `acp run BatchOptimize`
- 对 PESsearch 候选或其他结构进行 per-item Opt/TS + 频率 + 单点能 + 热力学修正
- 四种 profile：`--profile opt_only|opt_freq|opt_freq_sp|opt_freq_sp_thermo`
- 支持 `--items-file` 批量输入、`--from-job` 继承上游产物
- TS 候选自动识别（TAG: TS），频率分析 + IRC 连通性检查

### 4. IRC — 端点验证 `acp run irc`
- 对 TS 结构运行 IRC（正向 + 逆向），发现反应端点
- 输出：`RESULT/irc/irc_forward.xyz` + `RESULT/irc/irc_reverse.xyz`
- 端点分类：connectivity fingerprint + mapped heavy-atom RMSD

### 5. Scan — 势能面扫描 `acp run scan`
- 柔性坐标扫描（distance/angle/dihedral），逐步优化 + 单点能
- 输出：`RESULT/trajectories/scan_trajectory.json` + 能量曲线
- 坐标格式：`--coordinate atom1,atom2,start,end`

### 6. 简单 ORCA 工作流 — `acp run singlepoint|opt|freq|...`
- 单点能计算（singlepoint）
- 几何优化（optimize）
- 频率计算（frequency）
- xTB 优化（xtb-optimize）

### 7. NMR 化学位移预测 — `acp run nmr` ✅
- GIAO + Boltzmann 平均 + DP4/DP5 立体归属
- Goodman DP5 模型 + FCHL 原子表示（可选）

### 7. Web 服务 — `acp run serve`
- FastAPI 后端（`/api/status`, `/api/backends`, `/api/workflows`, `/api/v1/...`）
- 任务提交、分子上传、任务管理 REST API
- ACP Workbench 前端（暗色主题，实时轮询）
- systemd 服务管理

### 8. 远程 LSF 执行 ✅
- SSH/SFTP 多节点连接池
- LSF 脚本生成 + bsub 提交 + bjobs 监控
- 增量代码同步 + 结果拉取
- 磁盘压力 + 保留期清理

### 9. 任务队列操作 ✅（2026-08-17 起）
- **暂停 / 恢复**：运行中任务可暂停（`PAUSED`），本地 SIGSTOP/SIGCONT 进程组冻结/复活、远程 LSF `bstop`/`bresume`；**暂停不释放内存/磁盘配额**，适合临时让出算力
- **断点续算**：失败/取消任务按检查点继续（`continue` 操作）
- **重新运行**：一键以同 spec 另起新任务（`{name}__rerun`）
- **清除**：单任务级联删除（jobs + stage_tasks + artifacts），支持按状态/项目/时间批量清除
- **任务详情**：阶段 stepper、错误详情（error + stderr 尾部）、产物摘要、恢复操作建议

队列操作端点：`GET /api/v1/jobs/{id}/detail` · `POST /api/v1/jobs/{id}/pause|unpause|continue|rerun` · `POST /api/v1/jobs/purge`

---

### 旧入口退役映射表

| 旧入口 | 替代方案 | 说明 |
|--------|----------|------|
| `acp run ensemble` | `acp run Confsearch --protocol censo-crest --refinement-policy screen` | CREST+CENSO P+S |
| `acp run energy` | `acp run Confsearch --protocol censo-crest --refinement-policy rank1` 或 `cumulative-99` | 构象能量排名 |
| `acp run xtbmd_censo_energy` | `acp run Confsearch --protocol xtbmd-censo` | xTB-MD+CENSO 全链路 |
| `acp run mechanism` | `acp run PESsearch` → `acp run BatchOptimize` → `acp run irc` | 机理研究拆分为独立阶段 |
| `acp run Lowconfirm` | `acp run BatchOptimize --profile opt_freq` → 粗优化确认 | 退役 |
| `acp run Highconfirm` | `acp run BatchOptimize --profile opt_freq_sp_thermo` → 精细优化确认 | 退役 |
| `acp run optfreq` | `acp run optimize` + `acp run frequency` 或 `acp run BatchOptimize` | 优化+频率 |
| `acp run optfreqsp` | `acp run BatchOptimize --profile opt_freq_sp` | 优化+频率+单点 |

---

## 包结构

```
src/
├── acp/                          # 统一模块 (~130 .py, ~65k 行)
│   ├── cli.py                    # 统一命令行入口 (2,608 行)
│   ├── catalog.py                # 计算方法元数据 (2,915 行)
│   │
│   ├── confsearch/               # 统一构象搜索 + 能量
│   │   ├── engine.py             # ConfsearchEngine：协议调度、质量门控
│   │   ├── contracts.py          # 协议特定约束和质量门控
│   │   ├── manifest.py           # confsearch_manifest.json 产物
│   │   ├── profiles.py           # light / default / high 资源档位
│   │   ├── selection.py          # 候选筛选与排序
│   │   ├── protocols/            # 四种协议实现
│   │   │   ├── xtb_crest.py      #   xtb-crest（CREST搜索 + DFT精修）
│   │   │   ├── xtb_md.py         #   xtb-md（xTB-MD采样 + DFT）
│   │   │   ├── censo_crest.py    #   censo-crest（CREST + CENSO排序）
│   │   │   └── xtbmd_censo.py    #   xtbmd-censo（xTB-MD + ISOSTAT + CENSO）
│   │   └── shared/               # 协议共享工具
│   │
│   ├── calculations/             # 计算基元和引擎
│   │   ├── contracts.py          # CalculationPlan / CalculationRequest / Checkpoint 等冻结数据类
│   │   ├── checkpoint.py         # 原子 JSON checkpoint 写入/加载（plan fingerprint 校验）
│   │   ├── executor.py           # CalculationPlanExecutor：步骤调度 + 坐标交接 + checkpoint resume
│   │   ├── plans.py              # build_simple_plan / build_batch_plan / build_irc_request
│   │   ├── primitives/           # run_singlepoint / run_optimize / run_frequency / run_scan / run_irc / ThermochemistryCalculator
│   │   ├── pes/                  # PESscan 核心：scan + engine + contracts + validation + path_analysis + path_selection + atom_mapping + bond_changes
│   │   ├── batch/                # BatchOptimizeEngine + models + loaders + singlepoint
│   │   └── irc/                  # IRC endpoint discovery + validation
│   │
│   ├── compat/                   # 遗留布局只读兼容层
│   │   └── legacy/               # manifests.py（历史 manifest 读取器）+ layouts.py（布局探测）
│   │
│   ├── results/                  # 统一结果清单读取 (result_manifest.json)
│   ├── storage/                  # 统一 v2 结果清单写入 (result_manifest.json)
│   │
│   ├── core/                     # 共享核心机制（无化学逻辑）
│   │   ├── models.py             # Structure, StructureRecord, StructureEnsemble
│   │   ├── workflow.py           # Stage, WorkflowSpec, WorkflowRunner
│   │   ├── state.py              # WorkflowState, EventLog (JSONL)
│   │   ├── registry.py           # 通用注册表模式
│   │   ├── config.py             # 配置加载/合并
│   │   └── utils.py              # 工具函数
│   │
│   ├── backends/                 # QC 后端适配层
│   │   ├── base.py               # 能力 Protocol 定义（GeometryOptimizer / SinglePointCalculator / ConformerSearcher / ...）
│   │   ├── capabilities.py       # 后端能力矩阵
│   │   ├── registry.py           # 后端注册表（register_backend / get_backend / require_backend）
│   │   ├── orca.py               # ORCABackend (optimize, sp, freq)
│   │   ├── crest.py              # CrestBackend (conformer search via search())
│   │   ├── xtb.py                # XTBBackend (optimize, sp)
│   │   ├── censo_backend.py      # CENSOBackend (P+S 排序筛选)
│   │   ├── isostat_backend.py    # IsostatBackend (构象聚类)
│   │   ├── molclus_backend.py    # MolclusBackend (构象搜索)
│   │   ├── external.py           # 外部工具 re-export
│   │   └── external_backend.py   # ExternalBackend (ISOSTAT + Shermo)
│   │
│   ├── workflows/                # 工作流模块（legacy：ensemble/energy/xtbmd_censo_energy 已退役 + nmr/simple + registry）
│   │   ├── nmr.py                # NMR 化学位移预测（GIAO + DP4/DP5）
│   │   ├── simple.py             # 简单 ORCA 工作流 (sp/opt/freq/scan/xtb-opt)
│   │   ├── registry.py           # 工作流注册表（CLI 子命令 → WorkflowSpec）
│   │   └── __init__.py           # PEP 562 懒加载 re-export
│   │
│   ├── chem/                     # 化学逻辑
│   │   ├── embedding.py          # SMILES→RDKit 3D, XYZ 工具
│   │   └── composition.py        # 组成分析 / recalc_hess 规范化
│   │
│   ├── intake/                   # 数据摄入
│   │   ├── models.py             # StructureAsset, StructureParseResult
│   │   ├── parsers.py            # XYZ/SDF/MOL/GJF/INP/SMILES 解析
│   │   └── storage.py            # 上传文件存储
│   │
│   ├── io/                       # 分子结构 I/O
│   │   └── structures.py         # StructureReader, StructureWriter
│   │
│   ├── api/                      # FastAPI 服务 (~5,000 行)
│   │   ├── server.py             # FastAPI app 工厂 + static 托管
│   │   ├── routes.py             # /api/status, /api/backends
│   │   ├── v1_routes.py          # v1 任务/分子/文件 API
│   │   ├── v2_routes.py          # v2 API
│   │   └── schemas.py            # Pydantic 模型
│   │
│   └── scheduler/                # 任务调度器 (~7,800 行)
│       ├── jobs.py               # JobSpec, JobState
│       ├── manager.py            # 生命周期管理
│       ├── runner.py             # 后台进程执行
│       ├── store.py              # SQLite 持久化
│       ├── provenance.py         # 事件溯源 + 审计日志
│       ├── artifacts.py          # 制品管理
│       ├── stage_tasks.py        # 阶段任务分解
│       ├── tasks.py              # 阶段工作流任务调度
│       ├── projects.py           # 项目管理
│       ├── files.py              # 文件操作
│       ├── logs.py               # 日志管理
│       ├── local_cleanup.py      # 本地磁盘清理
│       ├── migrations.py         # 数据库迁移
│       ├── events.py             # 事件模型
│       └── remote/               # 远程 LSF 执行 (11 文件)
│           ├── runner.py         # 远程作业提交/轮询
│           ├── ssh.py            # SSH 连接池
│           ├── sftp.py           # SFTP 文件传输
│           ├── sync.py           # 增量代码同步
│           ├── node_manager.py   # 节点状态管理
│           ├── monitor.py        # LSF 作业监控
│           ├── fetcher.py        # 结果拉取
│           ├── cleanup.py        # 远程磁盘清理
│           ├── script_gen.py     # LSF 脚本生成
│           └── config.py         # 远程执行配置
│
└── cccp/             # Computational Chemistry Connection Package (底层 QC 接口库)
    ├── config.py                 # 6 源 YAML 配置（读 ~/.cccp.yaml，回退 ~/.conformer_search.yaml）
    ├── version.py                # __version__（与 __init__.py / pyproject.toml 三处同步）
    ├── core/                     # ConformerEngine (1,764 行) + ProtocolSpec + state_manager
    ├── qc/interfaces/            # ORCA / CREST / xTB 子进程封装（crest.py / orca.py / xtb.py 独立文件）
    ├── qc/runners/               # ISOSTAT/Shermo 运行器
    ├── qc/cluster/               # Local + LSF 适配器
    ├── io/                       # MolecularInputHandler
    └── utils/                    # 文件 I/O, 常量, 几何工具
```

### 设计原则

- **core/ 只放通用机制**：数据模型、工作流引擎、状态管理、注册表——不含任何化学特定逻辑
- **能力协议**：QC 后端通过 Protocol 声明能力（GeometryOptimizer, FrequencyCalculator 等），而非巨型 ABC
- **函数式 Stage 管道**：工作流由 Stage 函数组装，支持灵活组合
- **底层 QC 库**：`cccp`（Computational Chemistry Connection Package）提供 ORCA/CREST/xTB/ISOSTAT/Shermo 子进程封装与配置加载，`acp` 工作流层在其之上构建；统一通过 `acp` CLI 入口使用

---

## 安装

### 依赖

- Python 3.10+
- RDKit >= 2022.09.1
- NumPy >= 2.1.0
- PyYAML >= 6.0

### 外部软件（至少一个可用）

| 软件 | 用途 | 路径配置 |
|------|------|----------|
| ORCA | 单点能 / 优化 / 频率 | `executables.orca.path` |
| CREST | 构象搜索 | `executables.crest.path` |
| xTB | 预优化 / SPH / ENSO | `executables.xtb.path` |
| ISOSTAT | 构象聚类 | `executables.isostat.path` |
| Shermo | 热力学修正 | `executables.shermo.path` |
| CENSO | ensemble 排序筛选 | `executables.censo.path` |

### 安装命令

```bash
# 安装包（推荐）
pip install -e .

# 安装 API 依赖（FastAPI + uvicorn + multipart）
pip install -e '.[api]'

# 安装远程执行依赖（paramiko）
pip install -e '.[remote]'

# 安装全部
pip install -e '.[api,remote,dev]'

# 安装开发依赖（pytest + pytest-cov + ruff + mypy）
pip install -e '.[dev]'
```

---

## 快速开始

### ACP 新入口（推荐）

```bash
# 查看帮助
acp --help

# === Confsearch — 统一构象搜索 + 能量 ===
acp run Confsearch --input "CCO" --protocol xtb-crest --refinement-policy screen --output ./out
acp run Confsearch --input "CCO" --protocol censo-crest --profile light --output ./out
acp run Confsearch --input "CCO" --protocol xtbmd-censo --refinement-policy rank1 --output ./out
acp run Confsearch --input "CCO" --protocol xtb-md --refinement-policy cumulative-99 --output ./out

# === PESsearch — 势能面搜索 ===
acp run PESsearch --from-job 20260823_001_Confsearch --output ./pes_out
acp run PESsearch --from-artifact RESULT/confsearch/confsearch_manifest.json --output ./pes_out

# === BatchOptimize — 批量优化确认 ===
acp run BatchOptimize --from-job 20260823_002_PESsearch --output ./batch_out
acp run BatchOptimize --items-file structures.xyz --profile opt_freq_sp_thermo --output ./batch_out

# === IRC — 端点验证 ===
acp run irc --input ts_structure.xyz --output ./irc_out

# === Scan — 势能面扫描 ===
acp run scan --input "CCO" --coordinate 3,4,1.0,3.0 --output ./scan_out

# === 简单 ORCA 工作流 ===
acp run singlepoint --input "CCO" --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c"
acp run frequency --input molecule.xyz

# === NMR 化学位移预测 ===
acp run nmr --input "CCO" --output ./nmr_results
acp run nmr --input "CCO" --backend orca --reference "13C=185.0" "1H=31.5"

# === Web 服务 ===
acp run serve --port 8765
```

### 底层 QC 库（cccp）

`cccp`（Computational Chemistry Connection Package）是 `acp` 之下的 QC 接口库，
不再提供独立 CLI 入口——所有计算均通过 `acp run <workflow>` 触发。

---

## CLI 选项

```
acp run Confsearch --input <SMILES或文件路径>
                   --output <输出目录>
                   --protocol <xtb-crest|xtb-md|censo-crest|xtbmd-censo>
                   --profile <light|default|high>
                   --refinement-policy <screen|rank1|cumulative-99|all>
                   --nproc --mem --config ...

acp run PESsearch --from-job <Confsearch job id>
                  --from-artifact <confsearch_manifest.json 路径>
                  --output <输出目录>
                  --nproc --mem --config ...

acp run BatchOptimize --from-job <PESsearch job id>
                      --from-artifact <result_manifest.json 路径>
                      --items-file <structures.xyz 路径>
                      --profile <opt_only|opt_freq|opt_freq_sp|opt_freq_sp_thermo>
                      --output <输出目录>
                      --nproc --mem --config ...

acp run irc --input <TS 结构文件路径>
            --output <输出目录>
            --direction <forward|reverse|both>
            --nproc --mem --config ...

acp run scan --input <SMILES或文件路径>
             --coordinate <atom1,atom2,start,end>
             --output <输出目录>
             --nproc --mem --config ...

acp run singlepoint|optimize|frequency|xtb-optimize --input <SMILES或文件路径>
                  --output <输出目录>
                  --method <method> --basis <basis>
                  --nproc --mem --config ...

acp run nmr --input <SMILES或文件路径> --output <输出目录>
            --backend <orca> --reference "13C=185.0" "1H=31.5"

acp run serve [--host <host>] [--port <port>] [--reload]
```

---

## 配置

### 配置文件方式（推荐）

```bash
# 生成配置模板
acp run singlepoint --input "CCO" --save-config my_config.yaml

# 编辑 my_config.yaml 调整参数
# 然后用该配置运行
acp run Confsearch --input "CCO" --config my_config.yaml
```

### 配置合并顺序（后覆盖前）

1. Python 内置默认值 `_get_default_config()`（**唯一权威源**）
2. `~/.cccp.yaml`（用户目录）
3. `./cccp.yaml`（项目目录）
4. `--config` 文件（命令行指定）
5. `CONFSEARCH_*` 环境变量
6. CLI 参数（`--nproc`, `--mem` 等）

> ⚠️ `config/defaults.yaml` 仅供参考——Python 内置函数 `_get_default_config()` 是唯一权威默认值源。

---

## 开发

### 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定模块测试
pytest tests/test_acp_backends.py -v
pytest tests/test_acp_workflows_nmr.py -v
pytest tests/test_acp_mechanism_study.py -v

# 运行 CENSO 集成测试
pytest tests/test_acp_censo_p5_acceptance.py -v

# 运行远程执行测试
pytest tests/test_remote_phase*.py -v

# 按标记筛选
pytest -m "not slow" -v
```

当前测试状态：**96 测试文件，涵盖 ACP 核心 + 工作流 + API + 调度器 + 远程执行**

### 代码质量

- core/ 不含任何化学特定逻辑 ✅
- 所有 `__init__.py` 仅含 re-exports ✅
- 配置源已统一（3→1）✅
- 死代码已清除（FunnelRunner, PipelineExecutor）✅
- 原子化文件写入（os.replace 防崩溃）✅
- 预提交：ruff lint + ruff-format + mypy (strict)

---

## 文件统计

| 项目 | 文件数 | 代码行数 |
|------|--------|----------|
| `src/acp/` | ~130 | ~65,000 |
| `src/cccp/` | 17 | ~5,200 |
| `tests/` | 96 | ~10,000+ |
| 合计 | ~229+ | ~80,000+ |

---

## 架构图

```
┌───────────────────────────────────────────────────────────────────┐
│  CLI                                                               │
│  acp run Confsearch|PESsearch|BatchOptimize|irc|scan|nmr|simple|serve │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  WorkflowRunner (acp/core/workflow.py)                            │
│  计算计划驱动管道                                                    │
└────────────────────────────┬──────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┬────────────────┐
        ▼                    ▼                    ▼                ▼
  ┌───────────┐        ┌──────────┐        ┌──────────┐    ┌──────────┐
  │Confsearch │        │PESsearch │        │BatchOptimize│  │irc/scan  │
  │  (统一构象 │        │ (路径搜索│        │(批量优化 │    │(IRC验证/ │
  │   搜索+能量)│       │  PES)    │        │ 确认)    │    │ 坐标扫描)│
  └─────┬─────┘        └────┬─────┘        └────┬─────┘    └────┬─────┘
        │                   │                   │               │
        │    confsearch_manifest.json           │               │
        │              pes_profile.json          │               │
        │                   result_manifest.json│               │
        └───────────────────┴───────────────────┴───────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│  QC Backends (acp/backends/)                                      │
│  ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus                   │
│  能力协议：GeometryOptimizer / SinglePointCalculator / ConformerSearcher│
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  cccp — QC 接口库（子进程封装 + 配置加载）                          │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  Scheduler (acp/scheduler/)                                        │
│  Job Manager → Local Runner / Remote LSF Runner                    │
│  Provenance / Artifacts / Store / Stage Tasks                      │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  FastAPI Server (acp/api/)                                         │
│  /api/status, /api/backends, /api/workflows, /api/v1/...          │
│  ACP Workbench Frontend                                            │
└───────────────────────────────────────────────────────────────────┘
```

---

## 链接

| 资源 | 位置 |
|------|------|
| Web 服务 | http://localhost:8765（启动 `acp run serve` 后）|
| 前端仪表盘 | `frontend/ACP_Workbench.html` / `ACP_Workbench_v2.html` |
| 开发文档 | `docs/`（CENSO 集成、MethodMeta、Simple Workflows） |

---

## 系统服务

```bash
sudo systemctl restart acp          # Reload after code changes
sudo systemctl start acp            # Start
sudo systemctl stop acp             # Stop
sudo journalctl -u acp -f          # Tail logs
```

- **Service**: `acp.service`
- **Config**: `/etc/systemd/system/acp.service`
- **User**: `<user>`
- **URL**: http://localhost:8765
- **Reload reminder**: After any code modification, run `sudo systemctl restart acp`.

---

## 许可证

MIT License
