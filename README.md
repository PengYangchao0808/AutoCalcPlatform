# ACP — 自动化化学计算平台

**Auto-Calc Platform (ACP)** 是一个模块化的 Python 计算化学平台，旨在降低量子化学计算的入门门槛，通过 CLI 和 Web 界面让不熟悉计算化学流程的研究人员也能轻松提交任务、查看进度、可视化结果。

---

## 开发状态

| 阶段 | 状态 | 内容 |
|------|------|------|
| **Phase 0** | ✅ 完成 | 底层 `cccp`（Computational Chemistry Connection Package）QC 接口库 |
| **Phase 1** | ✅ 完成 | 模块化重构 + `acp` 统一模块 + Confsearch/PESsearch/BatchOptimize/irc/scan 计算工作流 + nmr/simple 工作流 + CENSO 集成 |
| **Phase 2** | ✅ 完成 | FastAPI Web 后端 + 任务调度器 + 远程 LSF 执行 |
| **Phase 4** | ✅ 完成 | 极简化重构：mechanism/ 删除，BatchOptimize/irc/scan 独立工作流 |
| **架构整改** | ✅ 完成（2026-10-05 收口） | 四层架构：`cccp/qc`（子进程+科学适配）→ `cccp/backends`（能力适配）→ `cccp/calculation`（任务层唯一执行驻点）→ `acp`（工作流/规划/发布；primitives/backends 为纯兼容转发）|

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
- 人工确认（2026-09-03 起）：Workbench 能量与轨迹查看器上手动增删/修改 TS、INT 选点后
  `POST /api/v1/jobs/{id}/pes/review` 写入 `RESULT/pes_search/pes_review.json`，
  物化 `RESULT/structures/*.xyz` 并更新 `RESULT/result_manifest.json`；
  历史 mechanism 任务保持只读（410）。详见 `docs/ACP_PES_Manual_Review_DevDoc.md`

#### 能量与轨迹查看器（Energy & Trajectory）

Workbench "能量与轨迹"标签页是 PES 扫描、几何优化、构象搜索、独立扫描的统一可视化入口：
- 统一 `TrajectoryFrame` 契约，所有视图共享节点/标注数据形状
- 通用帧操作：查看结构 / 锁定 / 导出 XYZ / 保存为候选（TS / INT / NONE）
- xtb-md / xtbmd-censo 协议额外提供采样历史三视图：能量轨迹、采样空间（MDS 二维散点）、覆盖度（饱和度指标 + 累计唯一曲线）
- 优化视图收敛面板：RMS/MAX 梯度和位移 vs 阈值达标判定
- IRC / NEB 视图已注册（IRC 能量曲线已接线：`irc_trajectory_v1` 正/反向双 series，运行中自动刷新，可从历史 `WORK/<stage>/ORCA` 轨迹只读回填；NEB 投影仍未实现）。IRC 逐帧结构浏览与路径动画由下方结构查看器提供

详见 `docs/ACP_Energy_Trajectory_Viewer_DevDoc.md`

#### 结构查看器（Structure Viewer）

Workbench "结构查看器"标签页（原 3D + 构象集合合并）在选中任务后自动加载结构目录：
- 按工作流自动解析：Confsearch 构象（能量 + Boltzmann 权重）、PES 推荐/人工确认（严格分组）、
  BatchOptimize 条目（含失败末帧）、scan 帧、IRC 正/反向逐帧、退役任务只读兼容模式
- 虚频可视化：频率列表、位移箭头、振动动画、TS 一阶鞍点证据提示（只读证据，判定归 Batch/IRC）
- 轻量几何编辑：键长/键角/二面角 + 撤销/重做 + 碰撞警告 + 另存为结构资产
- 结构叠合 RMSD（同序直配 / RDKit MCS 唯一映射 / 无法证明则清除跨结构测量）
- IRC 路径动画、大体系线框降级、视图状态（相机/样式/测量）本地持久化

结构查看器端点：`GET /api/v1/jobs/{id}/structure-viewer` · `GET .../entries/{e}/geometry` ·
`GET .../entries/{e}/vibrations` · `GET .../structure-viewer/overlay?entry_a=&entry_b=`

详见 `docs/ACP_Structure_Viewer_DevDoc.md`

### 3. BatchOptimize — 批量优化确认 `acp run BatchOptimize`
- 对 PESsearch 候选或其他结构进行 per-item Opt/TS + 频率 + 单点能 + 热力学修正
- 四种 profile：`--profile opt_only|opt_freq|opt_freq_sp|opt_freq_sp_thermo`
- 支持 `--items-file` 批量输入、`--from-job` 继承上游产物（读取
  `<PES任务>/RESULT/result_manifest.json` 中人工确认的有效候选）、
  `--select pes_ts_frame_027,...` 按稳定 candidate_id 筛选
- 前端 batch_structures 提交在 runner 层自动物化为 items 文件（2026-09-03 起）
- TS 候选自动识别（TAG: TS），频率分析 + IRC 连通性检查

### 4. IRC — 端点验证 `acp run irc`
- 对 TS 结构运行 IRC（正向 + 逆向），发现反应端点
- 输出：`RESULT/irc/irc_forward.xyz` + `RESULT/irc/irc_reverse.xyz`
- 端点分类：connectivity fingerprint + mapped heavy-atom RMSD

### 5. Scan — 势能面扫描 `acp run scan`
- 柔性坐标扫描（distance/angle/dihedral），逐步优化 + 单点能
- 输出：`RESULT/trajectories/scan_trajectory.json` + 能量曲线
- 坐标格式：`--coordinate atom1,atom2,start,end`

### 6. 简单 ORCA 工作流 — `acp run singlepoint|optimize|frequency|...`
- 单点能计算（singlepoint）
- 几何优化（optimize）
- 频率计算（frequency）
- xTB 优化（xtb_optimize）

### 6a. PES2TS 执行统一 — `acp run XtbPathSearch | OrcaGradient`
- `XtbPathSearch`：GFN2-xTB PATH metadynamics（消费冻结 `pes2ts_xtb_path_request_v1` payload，`--path-config`）
- `OrcaGradient`：ORCA 单点解析梯度 EnGrad（消费冻结 `pes2ts_orca_gradient_request_v1` payload，`--gradient-config`）
- 两者均为本地专用（不在远程允许集）

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
- **重新运行**：原地重跑（同任务目录、attempt+1；旧 attempt 回执归档至 `WORK/00_RUNTIME/attempts/`，不再另起 `{name}__rerun` 新任务）
- **清除**：单任务级联删除（jobs + stage_tasks + artifacts + tasks 索引行），支持按状态/项目/时间批量清除
- **任务详情**：阶段 stepper、错误详情（error + stderr 尾部）、产物摘要、恢复操作建议
- **分组浏览**：按分子/备注/任务类型/提交批次分组（默认分子折叠）；多维筛选（状态/工作流/标签/分子/批次，同维并集、跨维交集）；排序（创建时间/完成时间/最近活动/名称自然排序）；手动标签、归档、批量操作；整项目计数不受分页影响
- **分子管理**：分子别名解析（大小写不敏感匹配）、合并建议、分组显示名设置
- **来源链路**：只读浏览任务上下游依赖关系（BFS ≤10 跳）

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
├── acp/                          # 工作流/规划/发布层 (~247 .py)
│   ├── cli.py                    # 统一命令行入口：run/doctor/serve；retired 子命令拦截
│   ├── catalog.py                # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS（≈4.3k 行）
│   │
│   ├── confsearch/               # 统一构象搜索 + 能量
│   │   ├── engine.py             # ConfsearchEngine：协议调度、质量门控
│   │   ├── contracts.py          # 协议特定约束和质量门控
│   │   ├── manifest.py           # confsearch_manifest.json 产物
│   │   ├── profiles.py           # light / default / high 资源档位
│   │   ├── selection.py          # 候选筛选与排序
│   │   ├── protocols/            # 四种协议实现（xtb_crest / xtb_md / censo_crest / xtbmd_censo）
│   │   └── shared/               # 协议共享工具
│   │
│   ├── calculations/             # 规划/发布层（执行在 cccp.calculation 任务核心）
│   │   ├── contracts.py          # CalculationPlan / CalculationRequest / Checkpoint 等冻结数据类
│   │   ├── checkpoint.py         # 原子 JSON checkpoint 写入/加载（plan fingerprint 校验）
│   │   ├── executor.py           # CalculationPlanExecutor：dispatch 到 cccp.calculation.run_* + checkpoint resume
│   │   ├── plans.py              # build_simple_plan / build_batch_plan / build_irc_request
│   │   ├── primitives/           # 纯兼容转发 → cccp/calculation/tasks/* + ACP 发布契约
│   │   ├── result_publication.py # RESULT/result_manifest.json 统一注册入口
│   │   ├── pes/                  # PESsearch 核心：scan + engine + contracts + validation + path_analysis + atom_mapping
│   │   ├── batch/                # BatchOptimizeEngine + models + loaders（底层 cccp.calculation.batch）
│   │   ├── irc/                  # IRC endpoint discovery + validation（转发 cccp/calculation/irc_endpoints）
│   │   └── tsmode/               # 选虚频 → 定向 OptTS 引擎
│   │
│   ├── compat/                   # 遗留布局只读兼容层（legacy/ manifests + layouts）
│   ├── results/                  # 统一结果清单读取 + TrajectoryFrame 帧契约 + 结构查看器
│   ├── storage/                  # 统一 v2 结果清单写入 (result_manifest.json)
│   │
│   ├── core/                     # 共享核心机制（无化学逻辑）
│   │   ├── models.py             # Structure, StructureRecord, StructureEnsemble
│   │   ├── workflow.py           # Stage, WorkflowSpec, WorkflowRunner
│   │   ├── state.py              # WorkflowState, EventLog (JSONL)
│   │   ├── registry.py           # 兼容 shim（→ cccp/core/registry）
│   │   ├── config.py             # 配置加载/合并
│   │   └── paths.py              # run_root 解析 + 慢文件系统哨兵
│   │
│   ├── backends/                 # 纯 re-export shim（→ cccp/backends）+ 隔离的 legacy batch 面
│   │
│   ├── workflows/                # 工作流编排（14 active）
│   │   ├── nmr.py                # NMR 化学位移预测（GIAO + DP4/DP5）
│   │   ├── simple.py             # 简单工作流 (singlepoint/optimize/frequency/scan/irc/xtb_optimize)
│   │   ├── pes_search.py         # PESsearch · batch_optimize.py · irc.py · tsmode.py
│   │   ├── xtb_path.py           # XtbPathSearch（pes2ts 冻结 payload）
│   │   ├── orca_gradient.py      # OrcaGradient（pes2ts 冻结 payload）
│   │   ├── registry.py           # 工作流注册表（CLI 子命令 → WorkflowSpec）
│   │   └── ensemble/energy/xtbmd 退役引擎 — 仍作 Confsearch 协议引擎复用
│   │
│   ├── chem/                     # 化学逻辑（RDKit embedding + composition）
│   ├── intake/                   # 数据摄入（models / parsers 6 格式 / storage）
│   ├── io/                       # 分子结构 I/O（StructureReader/Writer，thin cccp wrapper）
│   │
│   ├── api/                      # FastAPI 服务
│   │   ├── server.py             # FastAPI app 工厂 + static 托管
│   │   ├── routes.py             # /api/status, /api/backends
│   │   ├── v1_routes.py          # v1 任务/分子/文件 API
│   │   ├── v2_routes.py + v2_structure_sources.py   # v2 API + 结构来源组织
│   │   └── schemas.py            # Pydantic 模型
│   │
│   └── scheduler/                # 任务调度器（jobs/manager/runner/store/stage_tasks/tasks + remote/ LSF 远程执行）
│
└── cccp/             # Computational Chemistry Connection Package（QC 底座，≈86 .py）
    ├── config.py                 # 6 源 YAML 配置（读 ~/.cccp.yaml，回退 ~/.conformer_search.yaml）
    ├── software.py               # 可执行解析单点（resolve_executable / discover_all）
    ├── version.py                # __version__（4 处同步：__init__ ×2 + acp/__init__ + pyproject）
    ├── core/                     # ProtocolSpec + CandidateSet + registry（ConformerEngine dormant）
    ├── qc/                       # ① QC 子进程层
    │   ├── interfaces/           # ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus 子进程封装
    │   ├── runners/ + cluster/   # Shermo/ISOSTAT 运行器；Local + LSF 适配器
    │   └── hessian_policy / method_meta / resolved_spec / shermo_adapter /
    │       thermo_normalize / translation / keyword_registry   # 共享科学适配（ qc/ 顶层）
    ├── backends/                 # ② 能力 Protocol 适配层（ORCA/CREST/xTB/CENSO/Isostat/Molclus；无 subprocess）
    ├── calculation/              # ③ 任务层 — 唯一执行驻点
    │   ├── tasks/                # 14 个任务核心（singlepoint/optimize/frequency/scan/irc/casscf/
    │   │                         #   thermochemistry/conformer_search/md_sampling/clustering/
    │   │                         #   censo_refine/nmr_shielding/xtb_path_search/orca_gradient）
    │   ├── requests.py + results.py   # typed options / payload 契约
    │   ├── _common.py + context.py + errors.py + selection.py + progress.py
    │   └── batch.py              # 通用批量执行器（并发/缓存/进度）
    ├── io/                       # MolecularInputHandler
    ├── pipeline/                 # PipelineExecutor（thin）
    └── utils/                    # 文件 I/O, 常量, 几何工具, 溶剂映射
```

### 设计原则

- **四层架构（2026-10-05 收口）**: ① `cccp/qc` 子进程接口 + 共享科学适配 → ② `cccp/backends` 能力 Protocol 适配（无 subprocess）→ ③ `cccp/calculation` 任务层（**唯一执行驻点**，14 个任务核心）→ ④ `acp` 工作流/规划/发布。`acp.calculations.primitives` 与 `acp.backends` 为纯兼容转发/shim；`cccp` 禁止导入 `acp`
- **core/ 只放通用机制**：数据模型、工作流引擎、状态管理、注册表——不含任何化学特定逻辑
- **能力协议**：QC 后端通过 Protocol 声明能力（GeometryOptimizer, FrequencyCalculator 等），实现体在 `cccp/backends/`；子进程封装在 `cccp/qc/interfaces/`
- **函数式 Stage 管道**：工作流由 Stage 函数组装，支持灵活组合
- **统一入口**：所有计算经 `acp run <workflow>` 触发（`python -m cccp` 不可用）；14 个工作流的执行全部经 `cccp.calculation` 任务核心

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
acp run xtb_optimize --input molecule.xyz

# === PES2TS 执行统一（本地专用） ===
acp run XtbPathSearch --path-config request.json --output ./path_out
acp run OrcaGradient --gradient-config request.json --output ./grad_out

# === NMR 化学位移预测 ===
acp run nmr --input "CCO" --output ./nmr_results
acp run nmr --input "CCO" --backend orca --reference "13C=185.0" "1H=31.5"

# === 初始化向导（交互式配置本地软件与远程计算节点） ===
acp init

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

acp run singlepoint|optimize|frequency|xtb_optimize --input <SMILES或文件路径>
                  --output <输出目录>
                  --method <method> --basis <basis>
                  --nproc --mem --config ...

acp run XtbPathSearch --path-config <pes2ts_xtb_path_request_v1 JSON> --output <输出目录>
acp run OrcaGradient --gradient-config <pes2ts_orca_gradient_request_v1 JSON> --output <输出目录>
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

### 数据目录（run_root）——两层目录约定

**安装目录 ≠ 数据目录**：所有任务产物（`WORK/`、`RESULT/`、QC 中间文件）与任务索引 `acp_jobs.db` 都落在数据目录 run_root，必须位于**原生文件系统**（WSL 下不要放 `/mnt/*`——9p 网络挂载会让 QC 子进程 I/O 慢约 1000 倍）。

解析优先级：`--run-root` CLI 参数 > `ACP_RUN_ROOT` 环境变量 > 平台默认（root → `/var/lib/acp/runs`；普通用户 → `~/.local/share/acp/runs`）。

```bash
# 查看生效的数据目录
acp run serve                       # 启动时打印生效 run_root；落在慢文件系统会 WARNING
ACP_RUN_ROOT=/data/acp/runs acp run serve

# 迁移历史数据（旧树保留只读归档，DB 自动备份 + 路径改写 + 校验）
python scripts/migrate_run_root.py ./ACP_runs /var/lib/acp/runs --dry-run
python scripts/migrate_run_root.py ./ACP_runs /var/lib/acp/runs
```

启动哨兵 `acp.core.paths.check_run_root_safety` 会对慢文件系统（9p/nfs/cifs/fuse 等）、与安装目录重叠、磁盘空间不足打警告（只警告不阻断；`ACP_ALLOW_SLOW_FS=1` 豁免）。

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

当前测试状态：**247 测试文件，≈5.7k 收集用例**，涵盖 cccp 任务层 + ACP 核心 + 工作流 + API + 调度器 + 远程执行 + 架构守护（`test_architecture_invariants.py`：唯一基元定义/依赖方向/能力证据表）

### 代码质量

- core/ 不含任何化学特定逻辑 ✅
- 所有 `__init__.py` 仅含 re-exports ✅
- 配置源已统一（3→1）✅
- FunnelRunner 已清除；`cccp/pipeline/` PipelineExecutor 保留为 thin 编排（dormant）✅
- 原子化文件写入（os.replace 防崩溃）✅
- 预提交：ruff lint + ruff-format + mypy (strict)

---

## 文件统计

| 项目 | 文件数 | 代码行数 |
|------|--------|----------|
| `src/acp/` | ~247 | ~105,000 |
| `src/cccp/` | 86 | ~35,000 |
| `tests/` | 247 | ~130,000 |
| 合计 | ~580 | ~270,000 |

---

## 架构图

```
┌───────────────────────────────────────────────────────────────────┐
│  CLI                                                               │
│  acp run Confsearch|PESsearch|BatchOptimize|XtbPathSearch|         │
│         OrcaGradient|irc|scan|nmr|simple|tsmode|casscf|serve       │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  WorkflowRunner (acp/core/workflow.py) + 工作流编排 (acp/workflows) │
│  计算计划驱动管道；单步经 CalculationPlanExecutor，多步走 batch engine │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  cccp/calculation — 任务层（唯一执行驻点，tasks/ 14 任务核心）        │
│  typed options/payload 契约 + 通用批量执行器 batch.py               │
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  cccp/backends — QC 能力适配层（acp/backends 为纯 re-export shim）  │
│  ORCA / CREST / xTB / CENSO / ISOSTAT / Molclus                    │
│  能力协议：GeometryOptimizer / SinglePointCalculator / ConformerSearcher│
└────────────────────────────┬──────────────────────────────────────┘
                             │
                             ▼
┌───────────────────────────────────────────────────────────────────┐
│  cccp/qc — QC 子进程层（interfaces/runners/cluster + 科学适配）      │
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
| Web 服务 | http://127.0.0.1:8765（启动 `acp run serve` 后；WSL 用户优先使用 127.0.0.1 避免 IPv6 问题）|
| 前端仪表盘 | `frontend/ACP_Workbench_v2.html`（v1 `ACP_Workbench.html` 为遗留只读）|
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
- **URL**: http://127.0.0.1:8765
- **Reload reminder**: After any code modification, run `sudo systemctl restart acp`.

---

## 许可证

MIT License
