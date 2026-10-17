# ACP 构象搜索统一与四阶段机理研究改造方案

**文档状态**：设计方案（v1.1：§5 补充权重来源矩阵、最终报告契约与其它工作流权重语义，与 2026-09-28 实现同步）
**版本**：v1.1
**日期**：2026-08-23（v1.1 修订：2026-09-28）
**适用项目**：ACP_V1_20260811

## 1. 目标

本方案统一 ACP 当前分散的构象相关入口，并将一次性完成的机理研究工作流改造成四个可独立提交、可人工交接、可断点续算的任务。

最终用户入口为：

```text
Confsearch
PESsearch
Lowconfirm
Highconfirm
```

总体流程：

```text
Confsearch
    ↓ s1_ensemble_manifest.json
PESsearch
    ↓ s2_path_manifest.json
Lowconfirm
    ↓ s3_lowconfirm_manifest.json
Highconfirm
    ↓ s4_highconfirm_manifest.json
```

核心原则：

1. `ensemble`、`energy` 和 `xtbmd_censo_energy` 不再作为独立工作流。
2. `Confsearch` 是唯一构象搜索入口，通过 protocol 选择具体计算路线。
3. `mechanism` 不再一次性执行 S1-S4。
4. S1-S4 各自是独立 Job，阶段之间通过标准 Manifest 和 Artifact 交接。
5. RPH 只保留为显式 parity backend，不作为正常用户入口或命名体系。
6. Lowconfirm 和 Highconfirm 共用一套确认引擎，只通过 profile 区分计算精度。
7. 新代码不得把 `job_id` 或 `study_id` 拼入磁盘路径。

## 2. 最终工作流入口

| Workflow ID | 中文名称 | 阶段 | 核心职责 |
|---|---|---:|---|
| `Confsearch` | 构象搜索 | S1 | 构象采样、去重、能量排序、Boltzmann 系综 |
| `PESsearch` | 势能面搜索 | S2 | 反应路径、TS 初猜和中间体初猜 |
| `Lowconfirm` | 粗优化 | S3 | 低精度优化、频率和初步 IRC 验证 |
| `Highconfirm` | 精细优化 | S4 | 高精度优化、频率、单点能和热力学 |

以下入口只保留历史兼容，不再允许新建：

```text
ensemble
energy
xtbmd_censo_energy
mechanism
mech-conf
mech-step
mech-confirm
mech-chain
```

## 3. Confsearch 统一设计

### 3.1 一个入口，同时产生构象和能量结果

一次 `Confsearch` 任务始终产生：

```text
构象文件
构象能量表
构象排序
Boltzmann 权重
统一构象 Manifest
```

不再保留以下重复关系：

```text
ensemble 只生成构象
energy 再次生成构象并计算能量
```

统一为：

```text
Confsearch
    = 构象采样
    + 构象能量
    + 系综统计
    + 可选高精度精修
```

即使用户只需要快速构象，任务也会输出基础能量表；是否执行高精度 DFT 精修由 `refinement_policy` 控制。

### 3.2 Confsearch Protocol

协议差异必须来自采样机制和主能量模型，不能只是同一条流程换名称。

| Protocol | 计算路线 | 类型 |
|---|---|---|
| `xtb-crest` | CREST → xTB → 去重 → Boltzmann | 纯 xTB 静态协议 |
| `xtb-md` | GFN-FF/xTB MD → xTB 优化 → 去重 → Boltzmann | 纯 xTB 动力学协议 |
| `censo-crest` | CREST → CENSO → 构象自由能 | CENSO 静态协议 |
| `xtbmd-censo` | GFN-FF MD → GFN1 → ISOSTAT → CENSO → DFT | 动力学高精度协议 |

#### 3.2.1 `xtb-crest`

```text
输入结构
→ CREST
→ GFN2-xTB 能量
→ 几何/拓扑去重
→ 相对能量排序
→ Boltzmann 权重
```

特点：

- 纯 xTB；
- 不调用 CENSO；
- 不调用 ORCA；
- 适合快速构象搜索和 PESsearch 前处理。

#### 3.2.2 `xtb-md`

```text
输入结构
→ GFN-FF 或 xTB MD
→ 多温度/多种子采样
→ GFN1/GFN2 优化
→ 构象去重
→ xTB 能量排序
→ Boltzmann 权重
```

特点：

- 纯 xTB；
- 与 `xtb-crest` 的区别是采样机制不同；
- 适合 CREST 难以覆盖的构象空间；
- 不调用 CENSO；
- 不调用 DFT。

当前 `xtbmd_censo_energy` 中的 GFN-FF MD、GFN1 batch optimization 和 ISOSTAT 能力可以迁移到该协议的公共采样层，但不应继续把 CENSO 和 DFT 固定在 `xtb-md` 中。

#### 3.2.3 `censo-crest`

```text
输入结构
→ CREST
→ CENSO 构象筛选
→ CENSO 能量和热力学分析
→ 构象排序
→ Boltzmann 系综
```

该协议统一旧的 `ensemble` 和 `energy` 入口。二者的区别改由 `refinement_policy` 表达，而不是两个工作流。

#### 3.2.4 `xtbmd-censo`

截图中的流程统一为一个完整协议：

```text
GFN-FF MD
→ GFN1/GFN2 批量优化
→ ISOSTAT 去重
→ CENSO 筛选
→ 选定构象 DFT 精修
→ 自由能计算
```

该协议不再拆成 `xtb-md`、`censo-md` 和 `xtbmd_censo_energy` 三个入口。

### 3.3 Refinement Policy

旧 `energy` 工作流中的 `rank1-only`、`full-ensemble`、`threshold=0.99` 和 `no-opt` 改为 Confsearch 的精修策略。

| Policy | 含义 |
|---|---|
| `screen` | 只完成协议自身的筛选和能量表 |
| `rank1` | 只精修最低能构象 |
| `cumulative-99` | 精修累计 Boltzmann 权重达到 99% 的构象 |
| `all` | 精修所有保留构象 |

`refinement_policy` 不改变采样路线，只决定精修范围。

合法组合示例：

```text
xtb-crest + screen
xtb-md + screen
censo-crest + rank1
censo-crest + cumulative-99
xtbmd-censo + rank1
xtbmd-censo + cumulative-99
```

### 3.4 Profile

Profile 只表示同一 protocol 内的计算精度，不创建新的 workflow。

```text
xtbmd-censo
├── light
├── default
└── high
```

Profile 可以改变：

- MD 温度、时间和种子数；
- 构象保留数量；
- CENSO 计算级别；
- DFT 方法和基组；
- 优化和频率精度；
- 热力学参数。

Profile 不得改变 protocol 的采样机制。

## 4. RPH S1 与 ACP S1 的处理

RPH 的 CENSO-lite S1 计算链路为：

```text
CREST
→ xTB 初筛
→ B97-3c 单点
→ xTB mRRHO
→ Boltzmann
```

它不是纯 xTB，因为包含 B97-3c 单点。

ACP 当前 `NativeCensoLiteProvider` 的目标科学链路与其基本一致；`XtbFastEnsembleProvider` 则是纯 xTB 路线。

新方案中不再把 RPH S1、ACP S1 和 xTB-fast 设计成多个用户入口，而统一为：

```text
Confsearch
├── protocol=xtb-crest
├── protocol=xtb-md
├── protocol=censo-crest
└── protocol=xtbmd-censo
```

RPH 只作为显式 parity backend：

```json
{
  "workflow": "Confsearch",
  "protocol": "censo-crest",
  "backend": "rph-parity"
}
```

生产默认使用：

```text
backend=native
```

RPH backend 不得对不支持的 protocol 静默降级。例如：

```text
backend=rph-parity + protocol=xtb-md
```

应直接报错，而不是错误地执行 CENSO-lite。

## 5. Confsearch 统一输出

所有 Confsearch protocol 使用统一输出目录：

```text
RESULT/
└── confsearch/
    ├── confsearch_manifest.json          ← S1 交接清单（schema confsearch_v1）
    ├── ensemble.xyz
    ├── ensemble.csv
    ├── energies.json
    ├── boltzmann.json
    ├── conformers/conf_NNNN.xyz
    ├── final_report.json                 ← 精修汇总报告（schema confsearch_final_report_v1）
    ├── final_conformers.xyz              ← 每个最终构象一帧（rank 序）
    └── quality_gates.json
```

> 历史文档中的 `refinement/` 目录从未被任何 writer 创建，已从产物流中删除；模块 docstring
> 已同步更正（`src/acp/confsearch/manifest.py:1-15`）。Manifest 内的 `refinement` 块
> （`policy`/`completed`/`artifacts`）与它无关，继续保留。
> `final_report.json` + `final_conformers.xyz` 是唯一的精修汇总产物
> （`src/acp/confsearch/report.py:1-13`）。

### 5.1 统一 Manifest（`confsearch_v1`，新增字段全部 additive）

```json
{
  "schema_version": "confsearch_v1",
  "workflow": "Confsearch",
  "protocol": "xtbmd-censo",
  "profile": "light",
  "refinement_policy": "rank1",
  "temperature_k": 298.15,
  "input": {
    "source": "CCO",
    "charge": 0,
    "multiplicity": 1,
    "input_hash": "sha256:..."
  },
  "sampling": {
    "method": "gfnff-md",
    "n_raw_frames": 500
  },
  "conformers": [
    {
      "conf_id": "conf_0001",
      "source_conf_id": "CONF1",
      "geometry": "conformers/conf_0001.xyz",
      "energy_hartree": -154.123,
      "free_energy_hartree": -154.117,
      "relative_energy_kcal": 0.0,
      "boltzmann_weight": 0.8442,
      "weight_source": "censo",
      "refined": true,
      "rank": 1
    }
  ],
  "selected_conformers": ["conf_0001"],
  "refinement": {
    "policy": "rank1",
    "completed": true,
    "artifacts": []
  },
  "weight_table": {
    "source": "censo",
    "method": "censo_table_rank1",
    "temperature_k": 298.15,
    "population_coverage": 1.0,
    "reference": "RESULT/ensembles/boltzmann_table.json"
  },
  "report": {
    "json": "confsearch/final_report.json",
    "xyz": "confsearch/final_conformers.xyz"
  },
  "provenance": {},
  "quality_gates": {}
}
```

字段语义（`src/acp/confsearch/manifest.py:199-250`；`src/acp/confsearch/contracts.py:80-160`）：

- 顶层 `temperature_k`：本次任务解析后的真实温度。载荷构建器在提供时写入
  （`manifest.py:212-214,238-239`），协议侧来源为 `ProtocolOutcome.temperature_k`
  （`contracts.py:143`），rank1 表可将它覆盖为表内温度（`protocols/_common.py:186-187`）。
  显式 `ConfsearchRequest.temperature` 在 `levels.thermo.temperature` 未固定时会以副本
  转发给 delegated 工作流（`levels.thermo.temperature` 与 `config.censo.temperature`
  两个通道，显式 levels 优先，绝不修改 request；`_common.py:227-275`）。
- `conf_id` 保持 `conf_NNNN`；`source_conf_id` 是原始筛选表键
  （`Structure.metadata["source"]` 优先，其次 `metadata["conf_id"]`），用于 join
  delegated 的 `RESULT/ensembles/boltzmann_table.json`
  （`protocols/_common.py:34-41`；`result_helpers.py:40-67`）。
- 逐构象 `weight_source`：`censo` | `xtb` | `dft` | `computed`，标记该条目权重来自哪张表
  （`contracts.py:99-101,112-114`）；`refined` 标记该条目是否进入细精度 DFT 精修
  （`result_helpers.py:101-105`）。G1 `boltzmann_weights_valid` 按 policy 判定：rank1 表模式
  要求每条权重落在 `(0,1]` 且与表值相等（容差 1e-9），rank1 降级模式只查 `0<w<=1`，
  其余 policy 保持 sum≈1；gates 中新增 `weight_source` 键（`result_helpers.py:150-227`）。
- `weight_table`：本次显示的权重所依据的表摘要，`source`/`method`/`temperature_k`/
  `population_coverage`/`reference`；`reference` 指向 delegated 的
  `boltzmann_table.json`（存在时）。仅 rank1 表模式会填充 `ProtocolOutcome.weight_table`；
  其余 policy 的条目权重由协议记录能量按同一公式重算（与 delegated 表同源），来源标签
  仍如实声明。非 dict 的 `weight_table` 会被警告并省略，不抛异常（`manifest.py:240-247`）。
- `report`：`json`/`xyz` 两个键，由引擎 finalize 阶段回填为
  `confsearch/final_report.json` / `confsearch/final_conformers.xyz`
  （`src/acp/confsearch/engine.py::_finalize`），与 result_manifest 注册路径一致。

### 5.2 `boltzmann.json`（附加键，旧键形状不变）

```json
{
  "weights": {"conf_0001": 0.8442},
  "weight_sum": 0.8442,
  "temperature_k": 298.15,
  "source": "censo",
  "method": "censo_table_rank1",
  "population_coverage": 1.0,
  "reference": "RESULT/ensembles/boltzmann_table.json"
}
```

- `weights` 以 manifest `conf_id` 为键，`weight_sum` 为求和，两者的值/形状与旧版完全一致。
- 附加键仅在调用方提供时写入（`manifest.py:92-107,169-183`）：`temperature_k`、
  `source`、`method`、`population_coverage`、`reference`。
- `boltzmann.json` 是 manifest 视图；rank1 的来源表（按 `source_conf_id` 键控）在
  delegated 的 `RESULT/ensembles/boltzmann_table.json`，两者不要混用。

### 5.3 最终报告契约（`confsearch_final_report_v1`）

`src/acp/confsearch/report.py` 写出 `final_report.json` + `final_conformers.xyz`：

`final_report.json`（`report.py:28,33-81`）：

| 层级 | 字段 | 说明 |
|---|---|---|
| 顶层 | `schema_version` | `confsearch_final_report_v1` |
| 顶层 | `workflow` / `protocol` / `profile` / `refinement_policy` | 请求回填 |
| 顶层 | `temperature_k` | 解析后的真实温度（`outcome.temperature_k`） |
| 顶层 | `weight_table{source, method, population_coverage, reference}` | 与 manifest 同源；`reference` 为 delegated `boltzmann_table_json`，经 `normalize_table_reference` 归一化为任务相对路径（如 `RESULT/ensembles/boltzmann_table.json`）（`report.py:33-50`） |
| 顶层 | `total_gibbs_hartree` / `total_gibbs_kcal_mol` | delegated 元数据存在时透传 |
| `conformers[]` | `conf_id` / `source_conf_id` / `rank` / `refined` | Manifest 口径 |
| `conformers[]` | `geometry` | confsearch 相对路径（与 manifest 相同） |
| `conformers[]` | `energy_hartree` / `free_energy_hartree` / `relative_energy_kcal` | 能量 |
| `conformers[]` | `energy_kind` | `refined=true` → `dft`，否则 `outcome.energy_kind` 或协议默认（纯 xTB → `xtb`，其余 → `censo`）（`report.py:121-125`） |
| `conformers[]` | `boltzmann_weight` / `weight_source` | 权重及来源（entry 优先，回退 outcome） |

`final_conformers.xyz`：每个 entry 一帧、rank 序；标题格式
`"{conf_id} G=<free_energy_hartree> w=<boltzmann_weight> src=<weight_source> [refined]"`。
几何文件缺失的条目跳过并记录 warning；全部缺失时抛 `FileNotFoundError`
（`report.py:141-188`）。

注册（`register_final_report`，`report.py:84-113`）：读取 `<mol_dir>/RESULT/result_manifest.json`
（缺失则新建），保留既有 header 字段与产品，`add_product` 后 merge 写回；重复注册按 product id
幂等。写入或注册失败直接抛出，绝不静默跳过；引擎 `_finalize` 直接调用
`write_final_report` + `register_final_report`，失败会让任务返回
`status="failed"` 并携带错误信息，不产出半成品清单。Screen 策略任务因此首次获得注册的
`result_manifest.json`。

| product id | kind | path |
|---|---|---|
| `confsearch_final_report` | `report` | `confsearch/final_report.json` |
| `confsearch_final_conformers` | `structure` | `confsearch/final_conformers.xyz` |

### 5.4 权重来源矩阵（protocol × refinement_policy）

| protocol | refinement_policy | weight_source / weight_method | population_coverage 与语义 |
|---|---|---|---|
| `xtb-crest` | `screen` | `xtb` / `xtb_table` | 1.0；CREST→GFN2-xTB 全量表即最终表（`protocols/_common.py:217-221`） |
| `xtb-crest` | `rank1` | `xtb` / `xtb_table` | 1.0；纯 xTB 无 DFT 阶段，非 screen 策略按 screen 处理并 warning（`contracts.py:182-188`；`protocols/xtb_crest.py:52-53`） |
| `xtb-crest` | `cumulative-99` | `xtb` / `xtb_table` | 1.0；同上 |
| `xtb-crest` | `all` | `xtb` / `xtb_table` | 1.0；同上 |
| `xtb-md` | `screen` | `xtb` / `xtb_table` | 1.0；MD→GFN1→ISOSTAT→能量窗过滤后的 xTB 表即最终表（`protocols/xtb_md.py:153-185`） |
| `xtb-md` | `rank1` | `xtb` / `xtb_table` | 1.0；非 screen 策略按 screen 处理并 warning |
| `xtb-md` | `cumulative-99` | `xtb` / `xtb_table` | 1.0；同上 |
| `xtb-md` | `all` | `xtb` / `xtb_table` | 1.0；同上 |
| `censo-crest` | `screen` | `censo` / `censo_table` | 1.0；CREST→CENSO 全量筛选表即最终表（`protocols/censo_crest.py:38-63`；`workflows/ensemble.py:398-401`） |
| `censo-crest` | `rank1` | 默认（`censo-light`/`censo-default`，含优化）：`censo` / `censo_table_rank1`；优化关闭（`censo.optimization.enabled=false`）的廉价分支：`xtb` / `xtb_table_rank1` | 1.0；权重 = 全量筛选表 `boltzmann_table.json` 的 `p₁`（按 `source_conf_id` join），不是重算的 1.0；表缺失/损坏降级为 `computed`/`computed` + warning，job 仍完成，G1 只查 `0<w<=1`（`_common.py:115-187`；`workflows/energy.py:468-495,580-594,677-681`） |
| `censo-crest` | `cumulative-99` | `dft` / `dft_table` | 已选 DFT 精修集合在筛选表中的累计占比（默认 0.99 阈值，通常 <1）（`_common.py:190-201`；`workflows/energy_shared.py:585-687,942-945`） |
| `censo-crest` | `all` | `dft` / `dft_table` | 1.0；`threshold_for_policy` 将 all 映射为 1.0，全选后 coverage 收敛为 1.0（`selection.py:54-64`） |
| `xtbmd-censo` | `screen` | `dft` / `dft_table`（delegated workflow-1 标签）；最终表为 no-opt CENSO 精修 gtot 表，跳过 DFT 移交 | 0.99 累计截断 → coverage ≤ 1；`censo-default` 档强制优化时全量、coverage 1.0（`protocols/xtbmd_censo.py:52,93-94`；`workflows/xtbmd_censo_energy.py:1304-1308,1834-1926`） |
| `xtbmd-censo` | `rank1` | 默认（含优化）：`censo` / `censo_table_rank1`；优化关闭：`xtb` / `xtb_table_rank1` | 1.0；同 censo-crest rank1 的表 join 与降级语义（`xtbmd_censo.py:52-53,91-92`；`xtbmd_censo_energy.py:1759-1832,1894-1908,1953-1992`） |
| `xtbmd-censo` | `cumulative-99` | `dft` / `dft_table` | 已选集合的累计占比（通常 <1）（`xtbmd_censo.py:95-96`） |
| `xtbmd-censo` | `all` | `dft` / `dft_table` | 1.0（threshold=1.0，全选） |

矩阵说明：

- 覆盖度语义：`population_coverage` = 本次显示的权重集合在完整筛选表中的累计占比。
  DFT 通路的 `cumulative-99` 常规小于 1（截断被披露），`all` 与 CENSO/xTB 表通路按定义为 1.0。
- 已知不对称（如实披露，不改选择科学）：`censo-crest` + `screen` 保留全量 CENSO 表
  （`censo`/`censo_table`，coverage 1.0）；`xtbmd-censo` + `screen` 走 `no_opt` 廉价路径，
  按 0.99 累计截断，其 delegated 元数据将最终 CENSO 精修表标记为 `dft`/`dft_table`
  （workflow-1 标签语义），coverage 随截断披露。
- 来源词表：`censo` | `xtb` | `dft` | `computed`（降级）。`computed` 仅出现在 rank1 表
  缺失/损坏的降级场景。

### 5.5 其它工作流的权重语义（不在本节产物流内）

| 工作流 / 视图 | 权重 | 说明 |
|---|---|---|
| NMR | 内部权重（CENSO/xTB gtot 级） | 仅持久化在 `nmr_report.json`（`src/acp/nmr/report.py:31,162`；`src/acp/nmr/models.py:219,313`），计算见 `src/acp/workflows/nmr.py:407-421,497`；结构查看器不展示，也不进入 `result_manifest.json` 产品 |
| BatchOptimize | 无 Boltzmann 权重 | 有 Gibbs 热力学修正（`gibbs_hartree` 写入 batch 清单，`src/acp/calculations/batch/engine.py:761-801`）；`src/acp/calculations/batch/` 内不存在任何 boltzmann/weight 字段 |
| PESsearch / scan / IRC / 采样历史三视图 | 无权重 | 帧与路径只携带能量和几何；`src/acp/calculations/pes/`、`src/acp/results/sampling_graph.py`、`src/acp/results/irc_projection.py` 均不产生 Boltzmann 权重，查看器条目的 `boltzmann_weight` 保持 `None` |

后续 PESsearch、NMR 和机理任务统一读取：

```text
confsearch_manifest.json
```

不再分别兼容新的 `ensemble.json`、`energy.json` 和 `xtbmd_result.json`。旧文件只保留历史读取能力。

## 6. 四阶段机理研究

### 6.1 Confsearch / S1

输入：

- 反应物、产物或中间体结构；
- 电荷和自旋多重度；
- Confsearch protocol；
- protocol 相关参数。

输出：

- 构象系综；
- 代表构象；
- 构象能量；
- Boltzmann 权重；
- `confsearch_manifest.json`。

用户可以在 S1 完成后确认：

- 反应物代表构象；
- 产物代表构象；
- 中间体代表构象；
- 是否使用其他 Confsearch protocol 重新搜索。

### 6.2 PESsearch / S2

输入：

- S1 `confsearch_manifest.json`；
- 反应物和产物状态；
- 原子映射；
- 反应坐标计划；
- 搜索策略。

允许的策略：

```text
guided-scan
reverse-peb
direct-ts
```

PESsearch 只负责：

```text
S1 构象
→ 反应坐标
→ 路径搜索
→ TS 初猜
→ 中间体初猜
```

不执行：

```text
TS 优化
频率
IRC
最终端点确认
```

输出：

```text
s2_path_manifest.json
path/
ts_guesses/
intermediate_guesses/
```

### 6.3 Lowconfirm / S3

输入 S2 候选，执行：

```text
低精度 Opt/OptTS
→ 独立 Frequency
→ 虚频检查
→ 可选 IRC
→ 初步端点判断
```

建议把 IRC 初步验证放在 Lowconfirm：

- PESsearch 负责发现；
- Lowconfirm 负责初步确认；
- Highconfirm 负责最终确认。

输出：

```text
s3_lowconfirm_manifest.json
optimized/
frequencies/
irc/
```

S3 完成后用户选择进入 S4 的候选。

### 6.4 Highconfirm / S4

输入 S3 候选，执行：

```text
高精度 Opt/OptTS
→ 独立 Frequency
→ Single Point
→ Thermochemistry
→ 可选最终 IRC
```

输出：

```text
s4_highconfirm_manifest.json
optimized/
frequencies/
single_points/
thermo/
mechanism_profile.json
```

最终结果包括：

- TS 几何；
- 虚频；
- 反应物、产物和 TS 能量；
- 正向和逆向能垒；
- Gibbs 自由能；
- IRC 连接关系；
- S3/S4 几何一致性；
- S3/S4 能垒趋势一致性；
- 完整 provenance。

## 7. Lowconfirm 和 Highconfirm 共用确认引擎

不实现两套独立科学代码：

```text
LowconfirmEngine
HighconfirmEngine
```

而使用：

```text
ConfirmEngine
├── LowConfirmProfile
└── HighConfirmProfile
```

公共内容：

- `StationaryPointRequest`；
- ORCA 输入生成；
- rescue matrix；
- 优化结果解析；
- 独立频率；
- canonical candidate 选择；
- Manifest；
- provenance；
- quality gate。

示例：

```python
LowConfirmProfile(
    opt_method="B97-3c",
    freq_method="B97-3c",
    sp_method="r2SCAN-3c",
    max_cycles=60,
)

HighConfirmProfile(
    opt_method="M062X",
    opt_basis="def2-SVP",
    freq_method="M062X",
    freq_basis="def2-SVP",
    sp_method="wB97M-V",
    sp_basis="def2-TZVPP",
    max_cycles=200,
)
```

## 8. 跨任务 Artifact 交接

标准 Artifact 引用：

```json
{
  "source_job_id": "20260823_001_Confsearch",
  "relative_path": "RESULT/confsearch/confsearch_manifest.json",
  "sha256": "sha256:...",
  "kind": "confsearch_manifest",
  "stage": "S1"
}
```

下一阶段不直接接收任意绝对路径，而接收：

```text
source_job_id
relative_path
sha256
kind
stage
```

服务端负责：

- 解析源 Job；
- 校验文件存在性；
- 校验 SHA256；
- 校验 Manifest 类型；
- 校验阶段关系；
- 校验候选 ID；
- 创建新的阶段 Job。

`job_id` 和 `study_id` 只存在于数据库、Manifest 和事件日志，不写入磁盘目录名。

## 9. 研究项目模型

建议新增 `MechanismProject`，用于关联多个独立 Job：

```python
@dataclass
class MechanismProject:
    project_id: str
    name: str
    reaction_definition_hash: str
    charge: int
    multiplicity: int
    created_at: str
    status: str
```

项目状态：

```text
created
s1_ready
s2_ready
s3_ready
completed
blocked
```

项目状态与单个 Job 状态分开。

单个 Job 仍使用现有生命周期：

```text
queued
running
paused
completed
failed
cancelled
```

人工选择不伪装成 `WAITING_REVIEW`。正确流程是：

```text
阶段 Job completed
→ 项目进入下一阶段 ready 状态
→ 用户选择候选
→ 用户显式提交下一阶段 Job
```

## 10. 文件布局

遵守当前 v1.2 文件布局，不重新引入：

```text
mechanism_study/<study_id>/
```

每个阶段 Job 使用自己的任务目录：

```text
<task_root>/
├── WORK/
│   ├── 00_RUNTIME/
│   ├── 01_PREPARE/
│   ├── 02_SEARCH/
│   ├── 03_OPT/
│   ├── 04_FREQ/
│   ├── 05_SP/
│   ├── 06_THERMO/
│   ├── 07_PATH/
│   └── 08_ANALYSIS/
├── RESULT/
├── job.json
├── task.json
├── events.jsonl
├── stdout.log
└── stderr.log
```

主要目录：

| 任务 | 主要目录 |
|---|---|
| `Confsearch` | `WORK/02_SEARCH` |
| `PESsearch` | `WORK/07_PATH` |
| `Lowconfirm` | `WORK/03_OPT`、`WORK/04_FREQ`、`WORK/07_PATH` |
| `Highconfirm` | `WORK/03_OPT`、`WORK/04_FREQ`、`WORK/05_SP`、`WORK/06_THERMO` |

## 11. CLI

### Confsearch

```bash
acp run Confsearch \
  --input "CCO" \
  --protocol xtb-crest \
  --refinement-policy screen \
  --output ./confsearch_out
```

```bash
acp run Confsearch \
  --input "CCO" \
  --protocol xtbmd-censo \
  --profile light \
  --refinement-policy rank1 \
  --output ./confsearch_out
```

### PESsearch

```bash
acp run PESsearch \
  --from-job 20260823_001_Confsearch \
  --from-artifact RESULT/confsearch/confsearch_manifest.json \
  --strategy guided-scan \
  --plan reaction_plan.json \
  --output ./pes_out
```

### Lowconfirm

```bash
acp run Lowconfirm \
  --from-job 20260823_002_PESsearch \
  --from-artifact RESULT/mechanism/s2_path_manifest.json \
  --select ts_guess_001,ts_guess_004 \
  --output ./lowconfirm_out
```

### Highconfirm

```bash
acp run Highconfirm \
  --from-job 20260823_003_Lowconfirm \
  --from-artifact RESULT/mechanism/s3_lowconfirm_manifest.json \
  --select ts_guess_001 \
  --output ./highconfirm_out
```

## 12. Catalog、API 和调度器

当前入口主要位于：

- `src/acp/catalog.py`；
- `src/acp/workflows/registry.py`；
- `src/acp/scheduler/stage_tasks.py`；
- `src/acp/scheduler/runner.py`；
- `src/acp/api/v1_routes.py`；
- `src/acp/api/v1_schemas.py`。

### 12.1 Catalog

新增 active：

```text
Confsearch
PESsearch
Lowconfirm
Highconfirm
```

标记 retired：

```text
ensemble
energy
xtbmd_censo_energy
mechanism
```

### 12.2 Confsearch Schema

```python
"Confsearch": {
    "fields": [
        "protocol",
        "profile",
        "refinement_policy",
        "temperature",
        "energy_window",
        "max_conformers",
    ],
    "protocols": [
        "xtb-crest",
        "xtb-md",
        "censo-crest",
        "xtbmd-censo",
    ],
}
```

### 12.3 StageTask

每个 Job 只显示自己的内部任务：

```text
Confsearch:
    prepare → sampling → energy → dedup → refinement → finalize

PESsearch:
    prepare → path_search → candidate_extract → finalize

Lowconfirm:
    prepare → optimize → frequency → irc → finalize

Highconfirm:
    prepare → optimize → frequency → single_point → thermo → finalize
```

不再使用单个 `mechanism` Job 的：

```text
S0 → S1 → S2 → S3 → SR → S4
```

## 13. 前端

工作流选择器只显示：

```text
构象搜索
势能面搜索
粗优化
精细优化
```

删除：

```text
构象生成
构象能量
xTB 动力学构象自由能
机理研究 / 过渡态
```

Confsearch 卡片内部显示：

```text
计算协议
├── xTB + CREST
├── xTB-MD
├── CREST + CENSO
└── xTB-MD + CENSO + DFT
```

对应：

```text
xtb-crest
xtb-md
censo-crest
xtbmd-censo
```

同时显示：

```text
精修策略
├── 仅筛选
├── Rank 1
├── 累计 Boltzmann 99%
└── 全部构象
```

机理研究页面显示：

```text
[✓] S1 Confsearch
[✓] S2 PESsearch
[ ] S3 Lowconfirm
[ ] S4 Highconfirm
```

不再显示：

```text
auto_converge
promote_to_s4
RPH S3
RPH S4
SR 自动推进
```

## 14. 代码目录和复用关系

建议新增：

```text
src/acp/confsearch/
├── __init__.py
├── engine.py
├── contracts.py
├── protocols.py
├── profiles.py
├── refinement.py
├── manifest.py
├── selection.py
├── protocols/
│   ├── xtb_crest.py
│   ├── xtb_md.py
│   ├── censo_crest.py
│   └── xtbmd_censo.py
└── shared/
    ├── deduplication.py
    ├── boltzmann.py
    ├── artifacts.py
    └── provenance.py
```

建议统一引擎：

```python
class ConfsearchEngine:
    def run(self, request: ConfsearchRequest) -> ConfsearchResult:
        ...
```

所有上层流程只负责构造请求，不再复制 CREST、CENSO、xTB 和构象去重流程。

当前实现迁移关系：

| 当前实现 | 新实现 |
|---|---|
| `src/acp/workflows/ensemble.py` | `protocols/censo_crest.py` |
| `src/acp/workflows/energy.py` | `refinement.py` + `censo_crest.py` |
| `src/acp/workflows/xtbmd_censo_energy.py` | `protocols/xtbmd_censo.py` |
| `NativeCensoLiteProvider` | `censo_crest` native backend |
| `XtbFastEnsembleProvider` | `xtb_crest` native backend |
| `xtbmd_md.py` | `xtb_md`/`xtbmd_censo` 公共采样层 |
| CENSO backend | `censo` energy evaluator |
| ORCA DFT handoff | `ConformerRefiner` |
| `module_step.py` | 拆分为 `PESsearch` 和 `Lowconfirm` |
| `module_confirm.py` | `Highconfirm` runner |
| `StudyOrchestrator` | 新流程不再调用，旧任务兼容 |

## 15. 质量门

### Confsearch / G1

```text
- 输入结构有效；
- 电荷和自旋有效；
- 至少一个有效构象；
- 构象去重完成；
- 能量排序有效；
- Boltzmann 权重有效；
- Manifest 和 provenance 完整。
```

### PESsearch / G2

```text
- 路径计算完成；
- 反应坐标有效；
- 至少一个 TS 或中间体初猜；
- 路径点和候选坐标完整；
- 候选来源可追溯。
```

### Lowconfirm / G3

```text
- 优化收敛；
- TS 为一阶鞍点；
- 频率结果有效；
- 虚频数量和模式合理；
- IRC 若启用则完成；
- 候选身份未错误塌缩。
```

### Highconfirm / G4-G5

```text
- 高精度优化收敛；
- 独立频率成功；
- 单点能成功；
- 热力学结果成功；
- S3/S4 几何一致；
- S3/S4 能垒趋势一致；
- 最终报告完整。
```

## 16. 兼容策略

### 新任务

旧入口应拒绝新建：

```text
The workflow has been retired.
Use Confsearch, PESsearch, Lowconfirm or Highconfirm.
```

### 历史任务

保留：

- 历史任务列表；
- 旧结果查看；
- 旧 Manifest 读取；
- 旧文件树显示；
- 历史报告展示。

不建议允许旧 `mechanism` 任务使用新参数重新运行，避免旧 S0-S4 自动链与新四阶段流程混用。

### 旧入口映射

| 旧入口 | 新入口映射 |
|---|---|
| `ensemble` | `Confsearch + censo-crest + screen` |
| `energy --rank1-only` | `Confsearch + censo-crest + rank1` |
| `energy --full-ensemble` | `Confsearch + censo-crest + cumulative-99` |
| `xtbmd_censo_energy --rank1-only` | `Confsearch + xtbmd-censo + rank1` |
| `xtbmd_censo_energy` 默认 | `Confsearch + xtbmd-censo + cumulative-99` |
| 机制 S1 | `Confsearch + xtb-crest` 或 `xtb-md` |
| NMR 构象生成 | 直接调用 `ConfsearchEngine` |

## 17. 实施顺序

### M1：统一 Confsearch 协议

完成：

- `ConfsearchRequest`；
- `ConfsearchResult`；
- `ConfsearchManifest`；
- protocol；
- profile；
- refinement policy；
- 统一输出格式。

### M2：合并 ensemble 和 energy

完成：

- `ensemble` 映射为 `censo-crest + screen`；
- `energy` 映射为 `censo-crest + rank1/cumulative-99`；
- 删除重复的 CREST/CENSO 调度；
- 统一能量表和 Manifest。

### M3：合并 xTB 动力学自由能

完成：

- `xtb-md` 纯 xTB 协议；
- `xtbmd-censo` 完整协议；
- 迁移 GFN-FF MD、GFN1、ISOSTAT、CENSO 和 DFT；
- 旧 `xtbmd_censo_energy` 退役。

### M4：拆分机理研究

完成：

- Confsearch 作为 S1；
- PESsearch 作为 S2；
- Lowconfirm 作为 S3；
- Highconfirm 作为 S4；
- 删除一次性 `mechanism` 新建入口。

### M5：API、调度器和远程运行

完成：

- Catalog；
- Registry；
- CLI；
- StageTask；
- Artifact Resolver；
- 远程脚本生成；
- continue/rerun；
- 旧 Job 只读兼容。

### M6：前端

完成：

- 一个 Confsearch 卡片；
- 四种 protocol；
- refinement policy；
- 四个机理阶段卡片；
- S2/S3 候选选择；
- 研究项目时间线。

### M7：测试和退役

完成：

- 新协议测试；
- 旧入口拒绝测试；
- Manifest 兼容测试；
- Artifact 校验测试；
- S1-S4 手动交接测试；
- RPH parity 测试；
- 历史任务读取测试。

## 18. 验收标准

必须满足：

- 新建任务界面只有一个 Confsearch 入口；
- `ensemble`、`energy` 和 xTB 动力学构象自由能不再作为独立入口；
- 截图中的完整流程对应唯一协议 `xtbmd-censo`；
- Confsearch 同时产生构象和能量结果；
- 协议之间采用不同采样/能量路线，避免重复实现；
- 纯 xTB 协议不调用 CENSO 和 ORCA；
- `xtbmd-censo` 作为单一完整协议，不拆成重叠协议；
- PESsearch 只负责势能面和候选生成；
- Lowconfirm 和 Highconfirm 共用确认引擎；
- `mechanism` 不再一次性执行 S1-S4；
- S2 和 S3 之间支持人工候选选择；
- 所有阶段使用标准 Artifact 和 Manifest；
- 旧任务可以查看，新任务不能使用旧入口；
- RPH 只用于 parity 对比，不出现在正常用户流程中。

最终结构：

```text
Confsearch
├── protocol=xtb-crest
├── protocol=xtb-md
├── protocol=censo-crest
└── protocol=xtbmd-censo

PESsearch
Lowconfirm
Highconfirm
```

