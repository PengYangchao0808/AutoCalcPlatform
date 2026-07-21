# ACP — 自动化化学计算平台

**Auto-Calc Platform (ACP)** 是一个模块化的 Python 计算化学平台，旨在降低量子化学计算的入门门槛，通过 CLI 和 Web 界面让不熟悉计算化学流程的研究人员也能轻松提交任务、查看进度、可视化结果。

---

## 开发状态

| 阶段 | 状态 | 内容 |
|------|------|------|
| **Phase 0** | ✅ 完成 | 遗留 `conformer_search` 单入口构象搜索管道 |
| **Phase 1** | ✅ 完成 | 模块化重构 + `acp` 统一模块 + 8 种工作流 + CENSO 集成 |
| **Phase 2** | ✅ 完成 | FastAPI Web 后端 + 任务调度器 + 远程 LSF 执行 |
| **Phase 3** | ✅ 完成 | NMR 高精度化学位移计算模块（ORCA GIAO） |
| **Phase 4** | ✅ 完成 | 机理研究模块（TS 搜索 + IRC 验证） |

---

## 功能概览

### 1. 构象搜索与热力学稳定性 — `acp run conformer` ✅
- SMILES / XYZ / GJF / LOG / OUT / SDF / MOL / ORCA INP 多格式输入
- CREST 构象搜索（单阶段 GFN2 / 两阶段 GFN0→GFN2）
- ISOSTAT 构象聚类
- CENSO 集成：censo-zero / censo-lite / censo-full / censo-full-safe 协议
- DFT 结构优化（ORCA wB97X-D4 / r2SCAN-3c / DLPNO-CCSD(T)）
- Shermo 热力学修正 + Boltzmann 加权
- 8 种精度/速度协议

### 2. CREST→CENSO Ensemble — `acp run ensemble` ✅
- CREST 构象搜索 → CENSO P+S 排序筛选
- 支持 censo-light / censo-default / censo-zero 预设
- 完整的 CENSO rcfile 模板注入

### 3. 自由能排名 — `acp run energy` ✅
- 从 CREST/CENSO ensemble 中提取 Boltzmann ≥99% 的子集
- ORCA 优化 + 频率 + 单点 + Shermo 热力学
- 支持 `--levels` 自定义计算级别
- 支持 `--no-opt` 快速 RSH//xTB 路径

### 4. 高精度 NMR 计算 — `acp run nmr` ✅
- 构象搜索 → ORCA GIAO NMR 屏蔽张量计算
- Gaussian/ORCA NMR log 解析
- Boltzmann 加权化学位移
- 多参考标准校准
- 报告输出：JSON / XLSX

### 5. 机理研究 — `acp run mechanism` ✅
- 反应物/产物/中间体构象搜索
- TS 初猜构筑（NEB 插值 / 反应坐标扫描）
- TS 优化（Opt=TS, CalcFC）
- IRC 验证 + 能垒分析

### 6. 简单 ORCA 工作流 — `acp run singlepoint|opt|freq|...` ✅
- 单点能计算
- 几何优化
- 频率计算
- 柔性扫描
- xTB 优化

### 7. 多协议基准测试 — `acp benchmark` ✅
- 多协议批量基准测试
- CSV 结果汇总 (energy, time, n_confs)

### 8. Web 服务 — `acp run serve` ✅
- FastAPI 后端（`/api/status`, `/api/backends`, `/api/v1/...`）
- 任务提交、分子上传、任务管理 REST API
- ACP Workbench 前端（暗色主题，实时轮询）
- systemd 服务管理

### 9. 远程 LSF 执行 ✅
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
│   │   ├── base.py               # QCBackend ABC + 8 种能力 Protocol
│   │   ├── capabilities.py       # GeometryOptimizer, SinglePointCalculator 等
│   │   ├── registry.py           # 后端注册表
│   │   ├── orca.py               # ORCABackend (optimize, sp, freq, nmr)
│   │   ├── crest.py              # CrestBackend (conformer search)
│   │   ├── xtb.py                # XTBBackend (optimize, sp)
│   │   ├── censo_backend.py      # CENSOBackend (P+S 排序筛选)
│   │   ├── isostat_backend.py    # IsostatBackend (构象聚类)
│   │   ├── molclus_backend.py    # MolclusBackend (构象搜索)
│   │   ├── external.py           # 外部工具 re-export
│   │   └── external_backend.py   # ExternalBackend (ISOSTAT + Shermo)
│   │
│   ├── workflows/                # 工作流模块
│   │   ├── conformer.py          # 构象搜索工作流
│   │   ├── ensemble.py           # CREST→CENSO ensemble 工作流
│   │   ├── energy.py             # 自由能排名工作流
│   │   ├── nmr.py                # NMR 化学位移工作流
│   │   ├── mechanism.py          # 机理研究工作流
│   │   ├── benchmark.py          # 多协议基准测试
│   │   ├── simple.py             # 简单 ORCA 工作流 (sp/opt/freq/scan)
│   │   └── registry.py           # 工作流注册表
│   │
│   ├── chem/                     # 化学逻辑
│   │   └── embedding.py          # SMILES→RDKit 3D, XYZ 工具
│   │
│   ├── intake/                   # 数据摄入
│   │   ├── models.py             # StructureAsset, StructureParseResult
│   │   ├── parsers.py            # XYZ/SDF/MOL/GJF/INP/SMILES 解析
│   │   └── storage.py            # 上传文件存储
│   │
│   ├── io/                       # 分子结构 I/O
│   │   └── structures.py         # StructureReader, StructureWriter
│   │
│   ├── nmr/                      # NMR 模块
│   │   ├── models.py             # NMRAtomShielding, NMRReport
│   │   ├── parser.py             # Gaussian/ORCA GIAO log 解析
│   │   └── calibration.py        # Boltzmann 平均 + 校准
│   │
│   ├── reports/                  # 报告序列化
│   │   └── nmr_report.py         # JSON / XLSX 输出
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
└── conformer_search/             # 遗留包 (完全保留,反向同步)
    ├── cli.py                    # 单入口 CLI
    ├── config.py                 # 6 源 YAML 配置
    ├── core/                     # ConformerEngine (1,764 行)
    ├── qc/interfaces/            # ORCA/CREST/xTB 子进程封装
    ├── qc/runners/               # ISOSTAT/Shermo 运行器
    ├── io/                       # MolecularInputHandler
    └── utils/                    # 文件 I/O, 常量, 几何工具
```

### 设计原则

- **core/ 只放通用机制**：数据模型、工作流引擎、状态管理、注册表——不含任何化学特定逻辑
- **能力协议**：QC 后端通过 Protocol 声明能力（GeometryOptimizer, FrequencyCalculator 等），而非巨型 ABC
- **函数式 Stage 管道**：工作流由 Stage 函数组装，支持灵活组合
- **向后兼容**：`conformer-search` CLI 完全保留，旧代码通过 `conformer_search` 包继续可用

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
| ORCA | 单点能 / 优化 / 频率 / NMR | `executables.orca.path` |
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

# === 构象搜索 ===
acp run conformer --input "CCO" --protocol ext --output ./results
acp run conformer --input molecule.xyz --protocol censo-full --output ./results
acp run conformer --batch-file molecules.txt --output ./batch_results

# === CREST→CENSO Ensemble ===
acp run ensemble --input "CCO" --output ./out
acp run ensemble --input "CCO" --preset censo-zero --output ./out

# === 自由能排名 ===
acp run energy --input "CCO" --output ./out
acp run energy --input "CCO" --no-opt --output ./out
acp run energy --input "CCO" --levels '{"opt":{"method":"wB97X-D4","basis":"def2-SVP"},"sp":{"method":"wB97X-D4","basis":"def2-TZVPPD"}}'

# === NMR 化学位移 ===
acp run nmr --input "CCO" --output ./nmr_results
acp run nmr --input "CCO" --backend orca --reference "13C=185.0" "1H=31.5"
acp run nmr --input molecule.xyz --temperature 298.0 --energy-window 5.0

# === 机理研究 ===
acp run mechanism --reactant "C=O" --product "C[O-]" --output ./mech_out
acp run mechanism --input reaction.xyz --n-irc-points 30

# === 简单 ORCA 工作流 ===
acp run singlepoint --input "CCO" --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c"
acp run frequency --input molecule.xyz

# === 多协议基准 ===
acp benchmark --input "CCO" --output ./bench_results
acp benchmark --input "CCO" --protocols ext censo-lite censo-zero

# === 查看可用协议 ===
acp protocol list
acp protocol info censo-full

# === Web 服务 ===
acp run serve --port 8765
```

### 旧入口（完全兼容）

```bash
# 与 ACP 新入口功能完全一致
conformer-search --input "CCO" --protocol ext --output ./results
conformer-search --batch-file molecules.txt --output ./batch_out
```

---

## CLI 选项

```
acp run conformer --input <SMILES或文件路径>
                  --output <输出目录>
                  --protocol <ext|censo-zero|censo-lite|censo-full|censo-full-safe|allopt|reference-sp|legacy-*>
                  --name <分子名称>
                  --nproc <CPU核心数>
                  --mem <内存限制，如32GB>
                  --config <自定义配置YAML>
                  --save-config <保存配置的路径>
                  --log-level <DEBUG|INFO|WARNING|ERROR>
                  --log-file <日志文件路径>

acp run ensemble --input <SMILES或文件路径>
                 --output <输出目录>
                 --preset <censo-light|censo-default|censo-zero>
                 --nproc --mem --config ...

acp run energy --input <SMILES或文件路径>
               --output <输出目录>
               [--no-opt] [--levels <JSON>]
               --nproc --mem --config ...

acp run nmr --input <SMILES或文件路径>
            --output <输出目录>
            [--backend orca] [--reference NUC=VALUE ...]
            [--temperature <K>] [--energy-window <kcal>]
            [--max-conformers <N>]

acp run mechanism --reactant <SMILES> --product <SMILES>
                  --output <输出目录>
                  [--n-irc-points <N>] [--method <method>]

acp run serve [--host <host>] [--port <port>] [--reload]
```

### 协议说明

| 协议 | 说明 | 速度 | 精度 |
|------|------|------|------|
| `ext` | 两阶段 CREST（GFN0→GFN2）+ ISOSTAT 聚类，输出候选 ensemble | 中 | 高 |
| `censo-zero` | 仅 CREST xTB 传递（无 CENSO） | 最快 | 低 |
| `censo-lite` | CREST + CENSO Part0/Part1/Part3（低精度 DFT SP 重排） | 快 | 中 |
| `censo-full` | CREST + CENSO 完整 Part0–Part3 筛选漏斗 | 慢 | 最高 |
| `censo-full-safe` | censo-full 的宽松窗口版（离子/活性体系） | 慢 | 最高 |
| `allopt` | 两阶段 CREST + 对所有候选做完整 DFT 验证 | 很慢 | 最高 |
| `reference-sp` | 对已有 ensemble 做 DLPNO-CCSD(T) 高精度单点 | 取决于规模 | 基准 |
| `legacy-*` | 旧版协议（保留用于结果复现，带 `legacy-` 前缀） | - | - |

> 旧版裸名 `full` / `lite` / `zero` / `benchmark` 已移除。请使用 `censo-*` 或 `legacy-*`。运行 `acp protocol info <name>` 可查看每个协议的具体阶段。

---

## 配置

### 配置文件方式（推荐）

```bash
# 生成配置模板
conformer-search --input "CCO" --save-config my_config.yaml

# 编辑 my_config.yaml 调整参数
# 然后用该配置运行
acp run conformer --input "CCO" --config my_config.yaml
```

### 配置合并顺序（后覆盖前）

1. Python 内置默认值 `_get_default_config()`（**唯一权威源**）
2. `~/.conformer_search.yaml`（用户目录）
3. `./conformer_search.yaml`（项目目录）
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
pytest tests/test_acp_workflows_conformer.py -v
pytest tests/test_acp_backends.py -v
pytest tests/test_acp_workflows_ensemble.py -v
pytest tests/test_acp_workflows_nmr.py -v
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
| `src/conformer_search/` | 17 | ~5,200 |
| `tests/` | 46 | ~8,000+ |
| 合计 | ~100+ | ~21,000+ |

---

## 架构图

```
┌─────────────────────────────────────────────────┐
│  CLI                                              │
│  acp run conformer|ensemble|energy|nmr|mechanism  │
│  acp run serve | acp benchmark                    │
│  conformer-search (legacy)                        │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  WorkflowRunner (acp/core/workflow.py)          │
│  Stage 管道：embed → crest → cluster →          │
│            opt → freq → sp → shermo → finalize  │
└────────────────────┬────────────────────────────┘
                     │
         ┌───────────┼───────────┬───────────┐
         ▼           ▼           ▼           ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ 构象搜索  │ │ Ensemble │ │ 自由能排名│ │ NMR 计算 │
   │ (Phase1) │ │ (Phase1) │ │ (Phase1) │ │ (Phase3) │
   └──────────┘ └──────────┘ └──────────┘ └──────────┘
         │           │           │           │
         └───────────┼───────────┼───────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  QC Backends (acp/backends/)                    │
│  ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus │
│  能力协议：GeometryOptimizer / FrequencyCalc... │
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
│  /api/status, /api/backends, /api/v1/...        │
│  ACP Workbench Frontend (frontend/)              │
└─────────────────────────────────────────────────┘
```

---

## 链接

| 资源 | 位置 |
|------|------|
| Web 服务 | http://localhost:8765（启动 `acp run serve` 后）|
| 前端仪表盘 | `frontend/ACP_Workbench.html` / `ACP_Workbench_v2.html` |
| 开发文档 | `docs/`（CENSO 集成、MethodMeta、Simple Workflows） |
| 基准报告 | `reports/benchmark_report.md` |

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
