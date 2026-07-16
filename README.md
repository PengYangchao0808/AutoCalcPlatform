# ACP — 自动化化学计算平台

**Auto-Calc Platform (ACP)** 是一个模块化的 Python 计算化学平台，旨在降低量子化学计算的入门门槛，通过 Web 界面让不熟悉计算化学流程的研究人员也能轻松提交任务、查看进度、可视化结果。

---

## 开发状态

| 阶段 | 状态 | 内容 |
|------|------|------|
| **Phase 1** | ✅ 完成 | 模块化重构 + 构象搜索核心引擎 + CLI 双入口 |
| Phase 2 | 🔜 计划中 | FastAPI Web 后端 + 任务队列 + 实时进度 |
| Phase 3 | 🔜 计划中 | NMR 高精度化学位移计算模块 |
| Phase 4 | 🔜 计划中 | 机理研究模块（TS 初猜 + 优化 + IRC 验证） |

---

## 功能规划

### 1. 热力学稳定性计算（构象搜索）— Phase 1 ✅
- SMILES / XYZ / GJF / LOG / OUT 多格式输入
- CREST 构象搜索（单阶段 GFN2 / 两阶段 GFN0→GFN2）
- ISOSTAT 构象聚类
- DFT 结构优化（ORCA）
- 高精度单点能计算（wB97X-D4 / DLPNO-CCSD(T)）
- Shermo 热力学修正 + Boltzmann 加权
- 8 种精度/速度协议：`ext` / `censo-zero` / `censo-lite` / `censo-full` / `censo-full-safe` / `allopt` / `reference-sp` / `legacy-*`

### 2. 高精度 NMR 计算 — Phase 3
- 构象搜索 → NMR 屏蔽张量计算（GIAO）
- Boltzmann 加权化学位移
- 多参考标准校准

### 3. 机理研究计算 — Phase 4
- 底物/产物/中间体构象搜索
- TS 初猜构筑（插值 / 反应坐标扫描）
- TS 优化（Opt=TS, CalcFC）
- IRC 验证 + 能垒分析

### 4. Web 前端 — Phase 2+
- 任务提交页面（SMILES 输入 / 参数配置）
- 实时进度查看（WebSocket 推送）
- 3D 分子结构可视化（3Dmol.js / NGL Viewer）
- 结果数据表格 + 能级图

---

## 包结构

```
src/acp/
├── core/                    # 共享核心机制层（无化学逻辑）
│   ├── models.py            # Structure, StructureRecord, StructureEnsemble
│   ├── workflow.py          # Stage, WorkflowSpec, WorkflowRunner
│   ├── state.py             # WorkflowState, EventLog (JSONL)
│   ├── registry.py          # 通用注册表模式
│   └── config.py            # 配置加载/合并
│
├── backends/                # QC 后端适配层
│   ├── base.py              # QCBackend ABC + 能力协议 (Protocol)
│   ├── registry.py          # 后端注册表 (register/get/require)
│   ├── orca.py              # ORCABackend
│   ├── crest.py             # CrestBackend
│   ├── xtb.py               # XTBBackend
│   └── external.py          # run_isostat, run_shermo
│
├── io/                      # 分子结构 I/O
│   └── structures.py        # StructureReader, StructureWriter
│
├── workflows/               # 工作流模块
│   └── conformer.py         # 构象搜索（7 stage 函数 + 5 协议 + Boltzmann）
│
├── api/                     # FastAPI 服务端 (Phase 2)
│   └── __init__.py          # 占位
│
└── cli.py                   # 统一命令行入口
```

### 设计原则

- **core/ 只放通用机制**：数据模型、工作流引擎、状态管理、注册表——不含任何化学特定逻辑（CREST/Shermo 等）
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
| xTB | 预优化 | `executables.xtb.path` |
| ISOSTAT | 构象聚类 | `executables.isostat.path` |
| Shermo | 热力学修正 | `executables.shermo.path` |

### 安装命令

```bash
# 安装包（推荐）
pip install -e .

# 安装开发依赖（pytest + pytest-cov）
pip install -e '.[dev]'
```

---

## 快速开始

### ACP 新入口（推荐）

```bash
# 查看帮助
acp --help

# 构象搜索（SMILES）
acp run conformer --input "CCO" --protocol ext --output ./results

# 构象搜索（XYZ 文件）
acp run conformer --input molecule.xyz --protocol censo-full --output ./results

# 批量处理
acp run conformer --batch-file molecules.txt --output ./batch_results

# 查看可用协议
acp protocol list
acp protocol info censo-full
```

### 旧入口（完全兼容）

```bash
# 与 ACP 新入口功能完全一致
conformer-search --input "CCO" --protocol ext --output ./results
```

---

## CLI 选项

```
acp run conformer --input <SMILES或文件路径>
                  --output <输出目录>
                  --protocol <ext|censo-zero|censo-lite|censo-full|censo-full-safe|allopt|reference-sp|legacy-ext|legacy-full|legacy-lite|legacy-zero|legacy-benchmark>
                  --name <分子名称>
                  --nproc <CPU核心数>
                  --mem <内存限制，如32GB>
                  --config <自定义配置YAML>
                  --save-config <保存配置的路径>
                  --log-level <DEBUG|INFO|WARNING|ERROR>
                  --log-file <日志文件路径>
```

### 协议说明

| 协议 | 说明 | 速度 | 精度 |
|------|------|------|------|
| `ext` | 两阶段 CREST（GFN0→GFN2）+ ISOSTAT 聚类，输出候选 ensemble | 中 | 高 |
| `censo-zero` | 仅 CREST + 低成本单点能排序 | 最快 | 低 |
| `censo-lite` | CREST + 低成本 DFT SP 重排（Part0/Part1/Part3） | 快 | 中 |
| `censo-full` | CREST + 完整 Part0–Part3 筛选漏斗 | 慢 | 最高 |
| `censo-full-safe` | censo-full 的宽松窗口版（离子/活性体系） | 慢 | 最高 |
| `allopt` | 两阶段 CREST + 对所有候选做完整 DFT 验证 | 很慢 | 最高 |
| `reference-sp` | 对已有 ensemble 做 DLPNO-CCSD(T) 高精度单点 | 取决于规模 | 基准 |
| `legacy-*` | 旧版协议（保留用于结果复现，带 `legacy-` 前缀） | - | - |

> 旧版裸名 `full` / `lite` / `zero` / `benchmark` 已移除，因为它们在 CENSO 与旧 ACP 之间含义冲突。请使用 `censo-*` 或 `legacy-*`。运行 `acp protocol info <name>` 可查看每个协议的具体阶段。

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
pytest tests/ -v

# 运行特定模块测试
pytest tests/test_acp_workflows_conformer.py -v
pytest tests/test_acp_backends.py -v
```

当前测试状态：**83/83 通过** ✅

### 代码质量

- core/ 不含任何化学特定逻辑 ✅
- 所有 `__init__.py` 仅含 re-exports ✅
- 配置源已统一（3→1）✅
- 死代码已清除（FunnelRunner, PipelineExecutor）✅
- 原子化文件写入（os.replace 防崩溃）✅

---

## Phase 1 重构成果

| 指标 | 变更前 | 变更后 |
|------|--------|--------|
| `__init__.py` 含实现代码 | runner 277行 + cluster 474行 | 0（全部提取到独立模块） |
| 配置源 | 3 源分歧（温度 298K vs 373K 等） | 1 源统一 |
| 死代码 | FunnelRunner（崩溃级），PipelineExecutor | 已删除 |
| QC 后端抽象 | CREST 不继承 ABC | CREST 继承 QCInterfaceBase |
| 文件原子写入 | `json.dump()` 存盘 | `os.replace()` 原子化 |
| XTBInterface 位置 | 与 CRESTInterface 同居 crest.py | 独立 xtb.py |
| 依赖项 | scipy/rich/tabulate/python-dotenv（未使用） | 已移除 |
| CLI 入口 | conformer-search（单一入口点错误） | `conformer-search` + `acp` 双入口 |

### 文件统计

| 项目 | 文件数 | 代码行数 |
|------|--------|----------|
| `src/acp/`（新增） | 21 | 2,501 |
| `tests/`（新增 ACP 测试） | 3 | ~800 |
| `src/conformer_search/`（保留） | 34 | ~3,500 |

---

## 架构图

```
┌─────────────────────────────────────────────────┐
│  CLI: acp run conformer / conformer-search       │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  WorkflowRunner (acp/core/workflow.py)          │
│  Stage 管道：embed → crest → cluster →          │
│            opt → freq → sp → shermo → finalize  │
└────────────────────┬────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ 构象搜索  │ │ NMR 计算  │ │ 机理研究  │
  │ (Phase1) │ │ (Phase3) │ │ (Phase4) │
  └──────────┘ └──────────┘ └──────────┘
        │            │            │
        └────────────┼────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  QC Backends (acp/backends/)                    │
│  ORCA / CREST / xTB                             │
│  能力协议：GeometryOptimizer / FrequencyCalc... │
└─────────────────────────────────────────────────┘
```

---

## 许可证

MIT License
