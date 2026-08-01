# ACP — 自动化化学计算平台

**Auto-Calc Platform (ACP)** 是一个模块化的 Python 计算化学平台，旨在降低量子化学计算的入门门槛，通过 CLI 和 Web 界面让不熟悉计算化学流程的研究人员也能轻松提交任务、查看进度、可视化结果。

---

## 开发状态

| 阶段 | 状态 | 内容 |
|------|------|------|
| **Phase 0** | ✅ 完成 | 底层 `cccp`（Computational Chemistry Connection Package）QC 接口库 |
| **Phase 1** | ✅ 完成 | 模块化重构 + `acp` 统一模块 + ensemble/energy/mechanism/simple 工作流 + CENSO 集成 |
| **Phase 2** | ✅ 完成 | FastAPI Web 后端 + 任务调度器 + 远程 LSF 执行 |
| **Phase 4** | ✅ 完成 | 机理研究模块（TS 搜索 + IRC 验证） |

> 注：conformer / nmr / benchmark 三个工作流已于 2026-07-27 移除（catalog 中保留为 `status:"retired"` 仅用于历史作业展示）。构象搜索能力由 `ensemble` / `energy` 工作流经 `CrestBackend.search()` 提供。

---

## 功能概览

### 1. CREST→CENSO Ensemble — `acp run ensemble` ✅
- SMILES / XYZ / GJF / LOG / OUT / SDF / MOL / ORCA INP 多格式输入
- CREST 构象搜索（经 `CrestBackend.search()` 后端层）
- CENSO 集成：P+S 排序筛选（censo-light / censo-default / censo-zero 预设）
- 完整的 CENSO rcfile 模板注入

### 2. 自由能排名 — `acp run energy` ✅
- 从 CREST/CENSO ensemble 中提取 Boltzmann ≥99% 的子集
- ORCA 优化 + 频率 + 单点 + Shermo 热力学（opt/freq same-level）
- 支持 `--levels` 自定义计算级别
- 支持 `--no-opt` 快速 RSH//xTB 路径
- DFT handoff 经 `CrestBackend` / `CensoBackend` 后端层（不再绕过直连 `CRESTInterface`）

> ⚠️ **注意事项（`--rank1-only` 模式）**：`acp run energy --rank1-only` 与未来的 `acp run xtbmd_censo_energy --rank1-only` 中，`G_total = G₁(fine DFT) + k_B·T·ln p₁` 的混合修正项采用 CENSO 权重表（半经验/低精度 DFT 级），而 G₁ 来自高精度 DFT，属于**混级量**：当 CENSO 排序与高精度方法不一致（rank 反转）时误差可达数 kcal/mol 量级。该模式仅适合"只看排名第一构象"的快速筛选场景；正式结果请使用默认全系综模式（完整混合式，DFT 级权重）。两种模式均输出 `total_gibbs_censo_hartree` 参考量用于对照修正。

### 3. 机理研究 — `acp run mechanism` ✅
- 反应物/产物/中间体构象搜索
- TS 初猜构筑（NEB 插值 / 反应坐标扫描）
- TS 优化（Opt=TS, CalcFC）
- IRC 验证 + 能垒分析

### 4. 简单 ORCA 工作流 — `acp run singlepoint|opt|freq|...` ✅
- 单点能计算（singlepoint）
- 几何优化（opt / optfreq / optfreqsp）
- 频率计算（freq）
- 柔性扫描（scan）
- xTB 优化（xtb-opt）

### 5. Web 服务 — `acp run serve` ✅
- FastAPI 后端（`/api/status`, `/api/backends`, `/api/workflows`, `/api/v1/...`）
- 任务提交、分子上传、任务管理 REST API
- ACP Workbench 前端（暗色主题，实时轮询）
- systemd 服务管理

### 6. 远程 LSF 执行 ✅
- SSH/SFTP 多节点连接池
- LSF 脚本生成 + bsub 提交 + bjobs 监控
- 增量代码同步 + 结果拉取
- 磁盘压力 + 保留期清理

---

## 包结构

```
src/
├── acp/                          # 统一模块 (~40 .py, ~8k 行)
│   ├── cli.py                    # 统一命令行入口 (1,835 行)
│   ├── catalog.py                # 计算方法元数据 (1,712 行)
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
│   ├── workflows/                # 工作流模块（4 个活跃 + registry）
│   │   ├── ensemble.py           # CREST→CENSO ensemble 工作流
│   │   ├── energy.py             # 自由能排名工作流（Boltzmann + DFT handoff）
│   │   ├── mechanism.py          # 机理研究工作流
│   │   ├── simple.py             # 简单 ORCA 工作流 (sp/opt/freq/optfreq/optfreqsp/scan/xtb-opt)
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
│   ├── api/                      # FastAPI 服务 (~1,600 行)
│   │   ├── server.py             # FastAPI app 工厂 + static 托管
│   │   ├── routes.py             # /api/status, /api/backends
│   │   ├── v1_routes.py          # v1 任务/分子/文件 API
│   │   └── schemas.py            # Pydantic 模型
│   │
│   └── scheduler/                # 任务调度器 (~2,700 行)
│       ├── jobs.py               # JobSpec, JobState
│       ├── manager.py            # 生命周期管理
│       ├── runner.py             # 后台进程执行
│       ├── store.py              # SQLite 持久化
│       ├── provenance.py         # 事件溯源 + 审计日志
│       ├── artifacts.py          # 制品管理
│       ├── stage_tasks.py        # 阶段任务分解
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

# === CREST→CENSO Ensemble ===
acp run ensemble --input "CCO" --output ./out
acp run ensemble --input "CCO" --preset censo-zero --output ./out

# === 自由能排名 ===
acp run energy --input "CCO" --output ./out
acp run energy --input "CCO" --no-opt --output ./out
acp run energy --input "CCO" --levels '{"opt":{"method":"wB97X-D4","basis":"def2-SVP"},"sp":{"method":"wB97X-D4","basis":"def2-TZVPPD"}}'

# === 机理研究 ===
acp run mechanism --reactant "C=O" --product "C[O-]" --output ./mech_out
acp run mechanism --input reaction.xyz --n-irc-points 30

# === 简单 ORCA 工作流 ===
acp run singlepoint --input "CCO" --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c"
acp run frequency --input molecule.xyz

# === Web 服务 ===
acp run serve --port 8765
```

### 底层 QC 库（cccp）

`cccp`（Computational Chemistry Connection Package）是 `acp` 之下的 QC 接口库，
不再提供独立 CLI 入口——所有计算均通过 `acp run <workflow>` 触发：

```bash
# 构象搜索能力现由 ensemble/energy 工作流经 CrestBackend 提供
acp run ensemble --input "CCO" --output ./results
acp run energy --batch-file molecules.txt --output ./batch_out
```

---

## CLI 选项

```
acp run ensemble --input <SMILES或文件路径>
                 --output <输出目录>
                 --preset <censo-light|censo-default|censo-zero>
                 --nproc --mem --config ...

acp run energy --input <SMILES或文件路径>
               --output <输出目录>
               [--no-opt] [--levels <JSON>]
               --nproc --mem --config ...

acp run mechanism --reactant <SMILES> --product <SMILES>
                  --output <输出目录>
                  [--n-irc-points <N>] [--method <method>]

acp run singlepoint|opt|freq|optfreq|optfreqsp|scan|xtb-opt --input <SMILES或文件路径>
                  --output <输出目录>
                  --method <method> --basis <basis>
                  --nproc --mem --config ...

acp run serve [--host <host>] [--port <port>] [--reload]
```

> 工作流/方法的可用选项与校验由 `catalog.METHOD_SCHEMAS` / `FIELD_DEFINITIONS` 驱动，可通过 `/api/v1/workflow-catalog` 与 `/api/v1/validate-method` 端点查询。

---

## 配置

### 配置文件方式（推荐）

```bash
# 生成配置模板
acp run singlepoint --input "CCO" --save-config my_config.yaml

# 编辑 my_config.yaml 调整参数
# 然后用该配置运行
acp run ensemble --input "CCO" --config my_config.yaml
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
pytest tests/test_acp_workflows_ensemble.py -v
pytest tests/test_acp_workflows_mechanism.py -v
pytest tests/test_acp_workflows_energy.py -v

# 运行 CENSO 集成测试
pytest tests/test_acp_censo_p5_acceptance.py -v

# 运行远程执行测试
pytest tests/test_remote_phase*.py -v

# 按标记筛选
pytest -m "not slow" -v
```

当前测试状态：**46 测试文件，涵盖 ACP 核心 + 工作流 + API + 调度器 + 远程执行**

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
| `src/acp/` | ~40 | ~8,000 |
| `src/cccp/` | 17 | ~5,200 |
| `tests/` | 46 | ~8,000+ |
| 合计 | ~100+ | ~21,000+ |

---

## 架构图

```
┌─────────────────────────────────────────────────┐
│  CLI                                              │
│  acp run ensemble|energy|mechanism|singlepoint|... │
│  acp run serve                                     │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  WorkflowRunner (acp/core/workflow.py)          │
│  Stage 管道（按工作流组合）                       │
└────────────────────┬────────────────────────────┘
                     │
          ┌───────────┼───────────┬───────────┐
          ▼           ▼           ▼           ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Ensemble │ │ 自由能排名│ │ 机理研究  │ │ Simple   │
    │ (Phase1) │ │ (Phase1) │ │ (Phase4) │ │ (Phase1) │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘
          │           │           │           │
          └───────────┼───────────┼───────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│  QC Backends (acp/backends/)                    │
│  ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus │
│  能力协议：GeometryOptimizer / ConformerSearcher│
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  cccp — QC 接口库（子进程封装 + 配置加载）       │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  Scheduler (acp/scheduler/)                      │
│  Job Manager → Local Runner / Remote LSF Runner  │
│  Provenance / Artifacts / Store                  │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  FastAPI Server (acp/api/)                       │
│  /api/status, /api/backends, /api/workflows,    │
│  /api/v1/...  ·  ACP Workbench Frontend          │
└─────────────────────────────────────────────────┘
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
