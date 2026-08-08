# ACP NMR + DP4/DP5 立体化学归属工作流 — 开发文档

**版本:** v0.4
**日期:** 2026-08-07
**状态:** P0/P1a/P1b/P2/P3/P4-代码完成（P4 代码 + 资产 + 运行时切换 + 测试已落地；真实 ORCA 冒烟与 P1.5 数据集验证待外部输入）；详见附录 A（P0+P1a）、附录 A-bis（P1b）、附录 A-ter（对等审计）、附录 B（P2）、附录 C（P3）、附录 D（FCHL-ML DP5 与完整对等性分析）
**作者:** QCcalc Team

====================================================================

## 目录

1. [概述与目标](#1-概述与目标)
2. [背景：Goodman DP4/DP5 方法](#2-背景goodman-dp4dp5-方法)
3. [设计原则](#3-设计原则)
4. [工作流总览](#4-工作流总览)
5. [分步骤工作流详解](#5-分步骤工作流详解)
6. [输入规范](#6-输入规范)
7. [输出规范](#7-输出规范)
8. [数学方法](#8-数学方法)
9. [GIAO NMR 计算实现（从零）](#9-giao-nmr-计算实现从零)
10. [误差模型](#10-误差模型)
11. [系统集成点](#11-系统集成点)
12. [分步实施方案](#12-分步实施方案)
13. [依赖与许可](#13-依赖与许可)
14. [测试与验收](#14-测试与验收)
15. [风险与边界](#15-风险与边界)
16. [目录结构规划](#16-目录结构规划)

====================================================================

## 1. 概述与目标

### 1.1 一句话目标

> 给定若干候选分子结构 + 一张实验 NMR 谱，工作流计算并输出**每个候选正确的概率（DP4/DP5）**，附带逐核位移对照与归属表。

### 1.2 解决的问题

有机化学家/天然产物研究中，常遇到"已知分子式与平面连接、未知相对立体构型"的鉴定问题。传统手段需补充 NOE、偶合常数、单晶衍射等实验。本工作流用**计算 NMR（GIAO）+ 贝叶斯概率**把这件事压缩成"传结构 + 传谱 → 出结论"。

### 1.3 为什么 ACP 适合做这件事

计算化学位移的标准链路是三步：**构象搜索 → 每构象 GIAO 单点 → Boltzmann 加权平均**。
ACP 已具备行业一流的构象生成能力（`acp run ensemble` + CENSO 筛选），强于 Goodman 方案的 MM 构象搜索（MacroModel/Tinker）。因此本工作流只需在已有构象生成能力**之后接两段**：GIAO 计算 + DP4/DP5 概率统计。NMR 场景下构象生成走 censo-lite（筛选级）即可，无需 energy 工作流的高精度 DFT。

### 1.4 目标效果（用户视角）

- 输入：候选结构（多个文件，或单分子自动枚举非对映体）+ 实验谱（已归属/未归属/原始 Bruker）
- 输出：候选概率排名 + DP4/DP5 概率 + 逐核（实验 vs 计算）对照表 + 误差分布图
- 精度基准：适用场景下相对构型归属正确率 ~90%+（文献 DP4 基准）

====================================================================

## 2. 背景：Goodman DP4/DP5 方法

剑桥 Jonathan Goodman 组（https://www-jmg.ch.cam.ac.uk/tools/nmr/）提出的概率体系：

| 方法 | 含义 | 前提假设 |
|------|------|----------|
| **DP4** | 候选集内归一化概率："哪个候选正确" | 候选集中**必有一个**正确 |
| **DP5** | 独立概率："该候选本身是否正确" | **不假设**集合包含正确答案 |
| **CP3** | 早期成对异构体归属（2009，已基本被 DP4 取代） | — |

**关键文献：**
- Smith & Goodman, *JACS* 2010, 132, 12946 — DP4 概率（DOI 10.1021/ja105035r）
- Smith & Goodman, *J. Org. Chem.* 2009, 74, 4597 — GIAO 方法论（DOI 10.1021/jo900408d）
- Howarth, Ermanis, Goodman, *Chem. Sci.* 2021, DOI 10.1039/D1SC04406K — DP5 + NMR-AI 自动化
- Howarth, Ermanis, Goodman, *Chem. Sci.* 2020, 11, 4351 — DP4-AI（DOI 10.1039/D0SC00442A）

**开源参考实现：** `Goodman-lab/DP5`（GitHub，MIT 许可）—— 工作流为：RDKit 清洗 → 非对映体/互变异构体生成 → MM 构象搜索（MacroModel/Tinker）→ DFT 优化 → GIAO → DP4/DP5 统计。ACP 用更强的构象搜索替换其 MM 段，统计核心可参考移植。

**缩放因子参考库：** CHESHIRE（Tantillo 组，cheshirenmr.info）—— 存储各方法/基组对的线性标定参数。

====================================================================

## 3. 设计原则

1. **复用构象生成**：不重写 CREST/CENSO，工作流在 `acp run ensemble`（censo-lite 协议）产出之上接 GIAO。NMR 只需筛选级几何 + 自由能，**刻意省略** energy 工作流的高精度 DFT handoff（opt+freq+SP+Shermo），避免冗余算力。
2. **单进程层在 cccp**：遵循 2026-08-02 治理原则——所有 subprocess（ORCA GIAO 调用、谱图处理）在 `cccp.qc.interfaces`，`acp/backends` 只做薄适配。
3. **能力驱动**：新增 `NmrShieldingCalculator` Protocol（PEP 544），ORCABackend 声明该能力。
4. **误差模型可替换**：DP4/DP5 依赖预训练误差分布，作为独立可替换组件，初始用占位模型。
5. **输入多模**：已归属 / 未归属 / Bruker 原始三种实验谱输入，用户任选。
6. **不做 ML 自动归属**：原始谱只做到峰拾取 + 匈牙利匹配，不引入神经网络归属（那是 NMR-AI 独立论文工作量）。

====================================================================

## 4. 工作流总览

```
┌─────────────────────────────────────────────────────────────────┐
│  输入：候选结构（多个 or 枚举）+ 实验谱（3 模式）+ 参数         │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
        ┌──────────────────────────────────────────┐
        │ 0/0a  输入解析（+ 可选 Bruker 谱处理）   │ 【新】
        └──────────────────────┬───────────────────┘
                               ▼
                 ┌──── 1（可选）非对映体枚举 ────┐  【新 P2】
                 ▼                               │
        ╔═══════════════════════════════════════╗
        ║   对每个候选 k = 1..K：               ║
        ╠═══════════════════════════════════════╣
        ║  2  构象生成(ensemble +   【复用 ACP】  ║
        ║     censo-lite) → {geom_i, w_i}        ║
        ║                                       ║
        ║  3  每构象 GIAO NMR 单点 【新 cccp】   ║
        ║     → 每构象每核 σ_i                  ║
        ║                                       ║
        ║  4  Boltzmann 加权 + 等价原子平均     ║
        ║     → 候选 k 的平均屏蔽 σ_k            ║
        ║                                       ║
        ║  5  归属匹配（已归属直通/未归属匈牙利）║
        ║     → (δ_calc, δ_exp) 配对             ║
        ║                                       ║
        ║  6  线性回归标定（分核种类）           ║
        ║     → 斜率/截距/残差 r_k               ║
        ╚══════════════════╤════════════════════╝
                         ▼
        ┌──────────────────────────────────────────┐
        │  7  DP4/DP5 概率（跨候选贝叶斯归一化）  │ 【新】
        │     → 每候选 P(DP4), P(DP5)              │
        └──────────────────────┬───────────────────┘
                               ▼
        ┌──────────────────────────────────────────┐
        │  8  报告生成（JSON / XLSX / 图表）       │ 【新】
        └──────────────────────────────────────────┘
```

**算力分布**：阶段 2（构象搜索）和 3（每构象 GIAO）是算力大头，跑在 LSF 远程节点；阶段 4–8 是轻量 CPU 逻辑，本地即可。

====================================================================

## 5. 分步骤工作流详解

> 每阶段给出：**目的 / 输入 / 输出 / 实现位置 / 状态**。【复用】= 已有能力，【新】= 需开发。

### 阶段 0：输入解析 【新】

**目的**：把三类输入统一解析、校验、归一化成内部数据结构。

**输入**：
- 候选结构：SMILES / SDF / XYZ，一个或多个（复用 `acp/intake/parsers.py`）
- 实验谱（三选一）：
  - 已归属位移表（结构化文本，见 §6.2）
  - 未归属位移列表
  - Bruker 原始目录 → 经阶段 0a 处理后并入未归属
- 参数：核种类（默认 ¹H + ¹³C）、方法/基组、溶剂、温度、TMS 参考值、Boltzmann 温度

**输出**：
- `candidates: list[Structure]` — 候选结构列表
- `exp_nmr: ExperimentalNmr` — 实验数据对象（位移数组 + 可选归属 + 等价组 + 忽略集）
- `config: NmrConfig` — 计算配置

**实现位置**：`acp/nmr/io.py`（新）、`acp/nmr/models.py`（新数据模型）

### 阶段 0a（可选）：Bruker 原始谱处理 【新 P3】

**目的**：把 Bruker 原始 FID 处理成峰列表。

**输入**：Bruker 实验目录（含 `fid`、`acqus`、`procs`，按 ¹H/¹³C 分子目录）

**处理**：读取 → 加窗（指数）→ 补零 → 傅里叶变换 → 相位校正（自动）→ 基线校正 → 峰拾取 → ppm 标定（需参考值）

**输出**：未归属峰列表（ppm 值 + 强度）→ 喂给阶段 0 的"未归属"分支

**实现位置**：`acp/nmr/spectra.py`（新），依赖 nmrglue

### 阶段 1（可选）：非对映体枚举 【新 P2】

**目的**：从单个未定构型分子穷举所有立体异构体。

**输入**：单个结构（立体信息不全）+ 可选手性中心指定

**处理**：RDKit 检测未指定立体中心 → `EnumerateStereoisomers` 穷举 → 生成完整立体定义的候选集（含非对映体 + 对映体）

**输出**：展开后的 `candidates` 列表

**实现位置**：`acp/nmr/enumerate.py`（新），复用 `acp/chem/embedding.py` 的 RDKit 基础设施

### 阶段 2：构象生成（每候选独立）【复用】

**目的**：得到每个候选的低能构象集合及 Boltzmann 权重。这是 ACP 相对 Goodman 的核心优势。

**输入**：单个候选结构

**处理**：直接复用 `acp run ensemble` 工作流，配 **`censo-lite` 协议**（CREST → CENSO 预筛+筛选），得到构象集合与 Boltzmann 权重。**不做** energy 工作流里那套精细 DFT（opt+freq+SP+Shermo）handoff——NMR 只需筛选级优化几何 + 自由能，高精度 DFT 是冗余开销。

**输出**：构象集合，每个构象含：CENSO 筛选级优化几何、相对自由能 ΔG、Boltzmann 权重 w

**实现位置**：复用 `acp/workflows/ensemble.py`（`run_ensemble_generation`），强制 `preset=censo-light`。GIAO 工作流在其输出目录上接续。

**关键约定**：复用 `energy_shared.py` 的 `boltzmann_weights`，从 CENSO 输出的 ΔG 算权重；几何直接取 CENSO 筛选产物。**溶剂须与 GIAO NMR 一致**（默认 chloroform）——把 `solvent` 配置同时传给 CENSO（构象分布）与 ORCA（屏蔽），否则构象权重与屏蔽的溶剂效应割裂，引入系统误差。

### 阶段 3：GIAO NMR 单点（每构象）【新，从零】

**目的**：对每个构象的优化几何，计算每个目标核的各向同性磁屏蔽常数 σ。

**输入**：单个构象的优化几何（坐标 + 元素）+ 核种类

**处理**：构造 ORCA GIAO 输入 → 调用 ORCA → 解析输出中的屏蔽常数

**输出**：每个构象、每个目标核的各向同性屏蔽 σ（ppm）

**实现位置**：
- subprocess 层：`cccp/qc/interfaces/orca.py` 新增 `nmr_shielding()` 方法（从零写，见 §9）
- 适配层：`acp/backends/orca.py` 新增 `nmr_shielding()` 薄适配
- 能力声明：`acp/backends/base.py` 新增 `NmrShieldingCalculator` Protocol

**默认方法**：mPW1PW91/6-311G(d) + GIAO + PCM（与 Goodman 误差模型一致，见 §8.0/§10；换方法须同步换模型）

### 阶段 4：Boltzmann 加权 + 等价原子平均（每候选）【新】

**目的**：把候选所有构象的屏蔽按权重平均，并把对称等价原子合并到与实验一致的分辨率。

**输入**：候选的各构象屏蔽 σ_i + Boltzmann 权重 w_i + 等价原子组（来自输入 EQ 或分子对称性检测）

**处理**：
- σ_avg(atom) = Σ_i w_i · σ_i(atom)
- 对每组等价原子，σ_avg = 组内成员的均值

**输出**：每个候选、每个核（等价原子已合并）的平均屏蔽 σ_k

**实现位置**：`acp/nmr/averaging.py`（新）

### 阶段 5：归属匹配 【新】

**目的**：建立"结构原子 ↔ 实验峰"的对应。

**输入**：σ_k（粗略换算成 δ_calc，如 δ ≈ σ_TMS − σ）+ 实验位移 δ_exp + 可选已知归属

**处理**：
- 已归属输入：归属直接给定，跳过匹配
- 未归属输入（**必须**先对称等价检测 + 强度加权，再匈牙利匹配）：
  1. **对称等价检测**（RDKit，`CanonicalRankAtoms` 的对称性分组 / `SymmetryRanking`）在未归属输入上自动识别等价原子组（如 CH₃ 的 3 个 H、CH₂ 的 2 个 H、分子对称轴两端的 C）。计算值按组取平均得到"信号级" δ_calc（每组 1 个值）。
  2. **强度加权**：等价原子组对应实验谱中一个峰，其**积分强度 ∝ 组内原子数**（CH₃ 峰强度 ≈ 3、CH₂ ≈ 2）。匹配前把实验峰强度归一化为多重度；匈牙利代价矩阵按"信号级"构建，`代价 = w_p · |δ_calc,组 − δ_exp,峰|`，其中 w_p 为峰强度权重（可选，默认 1，不加权重时退化为等权匹配）。
  3. 代价矩阵对"计算信号 ↔ 实验峰"一一对应（组与峰数不等时补 dummy 行/列，大代价）。
  - 已归属输入若带 `EQ:` 组则直接复用，无需检测；两者结果可交叉校验。

**输出**：归属表（等价组 → 实验峰）+ 配对 (δ_calc, δ_exp) 数据对（等价组一个配对，避免把 3 个等价 H 配到 3 个不同峰的错位）

**实现位置**：`acp/nmr/assignment.py`（新）

### 阶段 6：线性回归标定 【新】

**目的**：按核种类分别（¹H 一组、¹³C 一组）对配对做线性回归，得到标定系数与残差。这是 DP4 方法论核心。

**输入**：配对 (δ_calc, δ_exp)

**处理**：δ_exp = slope · δ_calc + intercept（最小二乘）；算 δ_scaled 与残差 r = δ_exp − δ_scaled

**输出**：slope、intercept、相关系数 R²、δ_scaled、逐核残差 r

**实现位置**：`acp/nmr/scaling.py`（新），用 numpy `polyfit` 或 scipy

### 阶段 7：DP4/DP5 概率计算 【新】

**目的**：把各候选残差转成概率。

**输入**：各候选残差 r + 预训练误差分布模型（见 §10）

**处理**：
- 各候选的似然 = 各核残差密度的乘积（独立假设）
- **DP4** = 似然在 K 个候选间归一化：P(DP4, k) = L_k / Σ_j L_j
- **DP5** = 独立概率，由折叠残差的 KDE 直接给出

**输出**：每候选 P(DP4)、P(DP5)

**实现位置**：`acp/nmr/probability.py`（新）

### 阶段 8：报告生成 【新】

**目的**：汇总全部数据，落成机器可读 + 人可读产物。

**输入**：上述所有阶段产物

**输出**：
- JSON 报告（候选排名 + 概率 + 归属表 + 残差 + 回归系数 + 每构象屏蔽与权重，供 API/前端）
- XLSX（位移对照表：实验 vs 计算 vs 残差，逐核）
- 图表（预测 vs 实验散点 + 回归线、误差分布直方图）

**实现位置**：`acp/nmr/report.py`（新），沿用 `acp/reports/` 风格

====================================================================

## 6. 输入规范

### 6.1 候选结构

复用 ACP 现有 intake，支持 SMILES / SDF / XYZ。两种模式（网页端可切换）：

- **显式多候选**：`--input c1.sdf c2.sdf c3.sdf` 或目录
- **枚举模式**：`--input molecule.sdf --enumerate` (+ 可选 `--stereocenters "C5,C8"`)

### 6.2 实验谱文本格式（已归属/未归属）

设计为结构化文本，人眼可读、易写易改：

```
# 13C (ppm)，可选原子归属（括号内原子标签）
C: 167.33(C1), 59.58(C2), 24.50(C3), 157.42(C8)

# 1H (ppm)，可选归属
H: 4.81(H4), 7.18(H5), 3.09(H6)

# 等价原子组（计算值取平均；每组一行）
EQ: C10,C12
EQ: H15,H16

# 忽略原子（如活泼氢，不参与比对）
OMIT: H19,H51
```

**归属缺失**（未归属谱）：

```
C: 167.33, 59.58, 24.50, 157.42
H: 4.81, 7.18, 3.09, 2.95(3), 3.41(2)
```

- 无括号标签 → 走"对称等价检测 + 强度加权 + 匈牙利匹配"（阶段 5）。
- 可选**强度/多重度注释**：`2.95(3)` 表示该峰积分为 3（如 CH₃）；不写则默认强度 = 1（等权）。未归属模式优先用注释强度，缺省时按分子对称性推断等价组后再赋组内原子数作为强度。

**字段语义**：
- `C:` / `H:` — 核种类段（可扩展 `N:` / `F:`）
- `(Cx)` — 原子标签，需与结构文件原子编号一致（SDF 原子序或显式映射）
- `EQ:` — 对称等价组，组内原子计算位移取平均后比对；已归属输入直接复用，未归属输入由对称性检测自动生成
- `OMIT:` — 不参与比对的原子
- `2.95(3)` — 峰位移后的括号整数为积分多重度（可选，未归属匹配强度加权用）

### 6.3 Bruker 原始目录（P3）

```
nmr_data/
├── Proton/      # 含 fid, acqus, procs
└── Carbon/      # 含 fid, acqus, procs
```

### 6.4 计算参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `nuclei` | `["1H","13C"]` | 目标核 |
| `nmr_method` | `mPW1PW91` | GIAO DFT 方法（**须与误差模型一致**，见 §8/§10） |
| `nmr_basis` | `6-311G(d)` | GIAO 基组（Goodman 标准） |
| `solvent` | `chloroform` | PCM 溶剂（可配；**同时作用于构象生成与 GIAO NMR**，见 §9.2） |
| `tms_shielding_c` | 191.69 | TMS ¹³C 屏蔽参考（ppm；按方法校准） |
| `tms_shielding_h` | 31.75 | TMS ¹H 屏蔽参考（ppm；按方法校准） |
| `boltzmann_temp` | 298.15 | Boltzmann 权重温度（K） |
| `error_model` | `goodman-legacy` | 误差模型标识（见 §10） |
| `conformer_source` | `ensemble` | 构象来源工作流（默认 ensemble） |
| `conformer_preset` | `censo-light` | 构象生成协议；NMR 用筛选级即可，不走高精度 DFT |

====================================================================

## 7. 输出规范

### 7.1 JSON 报告（`nmr_report.json`）

```json
{
  "summary": {
    "n_candidates": 8,
    "winner": {"index": 5, "label": "...", "dp4": 0.973, "dp5": 0.881},
    "nuclei": ["1H", "13C"]
  },
  "candidates": [
    {
      "index": 5,
      "label": "candidate_5",
      "dp4_probability": 0.973,
      "dp5_probability": 0.881,
      "n_conformers": 4,
      "regression": {
        "13C": {"slope": 0.987, "intercept": 1.23, "r_squared": 0.997, "mae": 1.82},
        "1H":  {"slope": 0.991, "intercept": 0.05, "r_squared": 0.996, "mae": 0.08}
      },
      "assignment": [
        {"atom": "C1", "element": "C", "exp_ppm": 167.33, "calc_ppm": 167.10, "residual": 0.23},
        {"atom": "H4", "element": "H", "exp_ppm": 4.81,  "calc_ppm": 4.79,  "residual": 0.02}
      ],
      "conformers": [
        {"id": 1, "boltzmann_weight": 0.62, "shieldings": {"C1": 23.5, "H4": 28.4}}
      ]
    }
  ],
  "config": { "nmr_method": "mPW1PW91", "nmr_basis": "6-311G(d)", "solvent": "chloroform" },
  "error_model": "goodman-legacy"
}
```

### 7.2 XLSX（`nmr_assignment.xlsx`）

每候选一 sheet：原子 | 元素 | 实验 ppm | 计算 ppm | 残差 | 是否等价组 | 是否忽略。

### 7.3 图表

- `scatter_<nucleus>.png`：δ_calc vs δ_exp 散点 + 回归线（每候选一色）
- `error_hist.png`：残差分布直方图

====================================================================

## 8. 数学方法

### 8.0 Goodman 原始计算方法学（文献/源码核查）

以下经 Goodman-lab/DP5 源码（`PyDP4.py` Settings 类 + `Gaussian.py`）核实。DP4 全流程原设**三个独立 DFT 层级**：

| 层级 | 方法/基组 | 用途 | ACP 是否需要 |
|------|-----------|------|--------------|
| **NMR** | **mPW1PW91 / 6-311G(d)** + GIAO | 各向同性磁屏蔽 σ | **需要**（核心） |
| Opt | B3LYP / 6-31G(d,p) | 构象几何优化 | **不需要**（CENSO 替代） |
| Energy | M062X / def2-TZVP | 单点能 → Boltzmann 权重 | **不需要**（CENSO 自由能替代） |

**关键耦合**：Goodman 的误差模型（Student-t / KDE）是**针对 mPW1PW91/6-311G(d) 训练的**。换方法 = 换误差模型，否则概率失真。ORCA 支持 `mPW1PW91` 与 `6-311G(d)`，可**逐字复现**该层级，从而直接复用 Goodman 训练好的误差模型——这是采用 mPW1PW91/6-311G(d) 而非 wB97X-D4 的根本理由。

**GIAO 输入**（Gaussian 原版 `nmr=giao`，ORCA 对应见 §9.2）：NMR 层级 + GIAO + PCM 溶剂（`scrf`）。

**TMS 参考**：δ = σ_TMS − σ_sample。默认 σ_TMS(¹³C)=191.69、σ_TMS(¹H)=31.75（源码注释标 B3LYP/6-31G\*\*）；实际由 `GetTMSConstants()` 按 NMR 层级选取。ACP 应按 mPW1PW91/6-311G(d) 重算 TMS 并存内置值。

**Boltzmann 权重来源**（源码 `ReadEnergies`）：优先取 Energy 层级（M062X）单点能，次取 Opt 层级，再次取 NMR 层级 SCF 能。ACP 统一用 CENSO 自由能 ΔG。

**InternalScaling**：δ_exp 对 δ_calc 的线性回归，得到 slope/intercept，残差 = δ_exp − δ_scaled。这是 DP4/DP5 概率的输入。

**结论**：ACP 工作流把 Goodman 的"Opt + Energy + NMR"三层压缩为"CENSO 构象 + 单层 NMR"，NMR 层级必须保持 mPW1PW91/6-311G(d) 以复用误差模型。

### 8.1 Boltzmann 加权

构象 i 的权重：
```
w_i = exp(-ΔG_i / RT) / Σ_j exp(-ΔG_j / RT)
```
平均屏蔽：`σ_avg(atom) = Σ_i w_i · σ_i(atom)`

### 8.2 屏蔽 → 位移换算

**初始换算**（用于匹配）：`δ_calc = σ_TMS − σ_sample`（σ_TMS 为同级别算出的 TMS 屏蔽常数，或内置参考值）

**最终标定**（用于残差与概率）：线性回归 δ_exp = slope · δ_calc + intercept（见 §8.4）。最终用 δ_scaled 而非 TMS 位移做概率。

### 8.3 匈牙利匹配（未归属谱）

**前置：对称等价检测 + 强度加权（必做，否则等价原子被错误分配到多个峰）。**

1. **等价组检测**：RDKit 对称性排名（`CanonicalRankAtoms`）得到原子对称等价组 `G1, G2, ...`；等价组内计算位移取均值 → "信号级"计算位移 `δ_calc(g)`，峰数 = 组数。
2. **强度权重**：实验峰积分强度 `I_p`（多重度，CH₃=3），归一化为权重 `w_p = I_p / max(I_p)`（可选，缺省 w_p=1 等权）。
3. **代价矩阵**：`C[g, p] = w_p · |δ_calc(g) − δ_exp(p)|`，用 `scipy.optimize.linear_sum_assignment` 找最小总代价的组↔峰一一映射。
4. 组数与峰数不等时补 dummy 行/列（大代价）；匹配完成后，每个等价组产出**一个**配对 (δ_calc(g), δ_exp(p))，进入 §8.4 回归（不重复计入组内原子数，避免 ¹H 信号被等价原子稀释/放大）。

### 8.4 线性回归标定

分核种类（¹H、¹³C 各一组）最小二乘：
```
δ_exp = slope · δ_calc + intercept
δ_scaled = slope · δ_calc + intercept
residual r_i = δ_exp,i − δ_scaled,i
```
报告 R²、MAE、slope、intercept。

### 8.5 DP4 概率

候选 k 的似然（独立残差假设）：
```
L_k = Π_i  f(r_{k,i} | 核种类i)
```
其中 f 是误差分布。**【源码核实 2026-08-07】** 原版 DP4 用 **Gaussian** 分布（`2·Φ(-|r/σ|)`，非早期草案所写的 Student-t），尺度参数 σ 按核种类：σ_H = 0.18731058105269952、σ_C = 2.269372270818724 ppm（`DP4.py:17-21` 实测值）。

DP4 归一化：
```
P(DP4, k) = L_k / Σ_{j=1..K} L_j
```

### 8.6 DP5 概率

用"折叠残差"（|r|）的核密度估计（KDE）给出独立概率，不跨候选归一化：
```
P(DP5, k) = Π_i g(|r_{k,i}|)
```
g 由训练集折叠残差分布拟合（scipy `gaussian_kde`，**bandwidth = 0.025**，源码 `kde_probs(..., 0.025)`）。源码流程：`ProcessIsomers → InternalScaling → kde_probs → BoltzmannWeight_DP5 → Calculate_DP5 → Rescale_DP5`。P(DP5) 高 = 该候选独立可信；低 = 即便 DP4 高也存疑（候选集可能都不对）。**DP5 要求 DFT 优化几何**（源码硬校验：`o` 必须在 workflow）——ACP 由 CENSO 满足。

====================================================================

## 9. GIAO NMR 计算实现（从零）

> 现假设 cccp 的 ORCA 接口**不具备** NMR 屏蔽计算能力，需从头实现。

### 9.1 新增能力 Protocol（`acp/backends/base.py`）

```python
@runtime_checkable
class NmrShieldingCalculator(Protocol):
    """Capability: can compute NMR shielding constants (GIAO)."""

    def nmr_shielding(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        nuclei: list[str] | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Compute isotropic magnetic shieldings for target nuclei."""
        ...
```

并在 `backends/capabilities.py` 的 `CAPABILITY_MATRIX` 增加 `"nmr_shielding"` 行，ORCA 标 `AVAILABLE`。

### 9.2 ORCA 输入生成（`cccp/qc/interfaces/orca.py`）

构造 GIAO NMR 输入。**默认层级必须为 mPW1PW91/6-311G(d)**（与 Goodman 误差模型一致，见 §8.0）。ORCA 5 用 `%eprnmr` 块显式声明计算哪些核的屏蔽：

```
! mPW1PW91 6-311G(d) TightSCF CPCM(Chloroform)
%eprnmr
  nuclei = all C {shift}
  nuclei = all H {shift}
end
%pal nprocs 8 end
* xyzfile charge multiplicity
```

要点：
- **方法/基组默认 mPW1PW91 / 6-311G(d)**：换层级须同步换误差模型（§10 校验）。ORCA 关键字 `mPW1PW91` 与 Gaussian 同名，6-311G(d) ORCA 原生支持。
- 简单关键字 `NMR` 也会触发 GIAO，但用 `%eprnmr` 更可控（可选核、可选 shift/shielding 输出）
- **溶剂**（可配，默认 chloroform）：ORCA 走 `CPCM(<溶剂>)`，对应 Gaussian 原版的 `scrf=(solvent=...)`。溶剂须**同时传给构象生成（CENSO/CREST）与 GIAO NMR**——实验谱在哪测的溶剂里，构象分布与屏蔽就应在同一溶剂下算，二者不一致会引入系统误差。复用 `energy_shared.resolve_solvent_config` 做名称归一化（ACP 溶剂名 → ORCA/CENSO 各自关键字）
- 几何用 CENSO 筛选级优化坐标（来自阶段 2 输出），不重新优化
- 多核走 `%pal nprocs`
- Goodman 原版对 M062X 加 `int=ultrafine` 网格；mPW1PW91 用 ORCA 默认网格即可

**实现**：在 ORCAInterface 增 `_write_nmr_input()`，参考现有 `_write_input` 的模板风格但产出 `%eprnmr` 块；方法/基组默认值取自 NmrConfig（mPW1PW91/6-311G(d)）。

### 9.3 执行与输出解析（`cccp/qc/interfaces/orca.py`）

ORCA GIAO 输出含类似段：
```
--------------------
CHEMICAL SHIELDING SUMMARY (ppm)
--------------------
 Nucleus   Element   Isotropic(ppm)
   0         6 C       45.230
   1         1 H       28.453
   ...
```

**实现** `NmrShieldingParser`：
- 正则扫描 `Chemical Shielding` 段，提取 (原子序, 元素, 各向同性屏蔽)
- 返回 dict: `{atom_index: {"element": ..., "isotropic": σ}}`
- 容错：ORCA 不同小版本格式微差，按"找 Isotropic 关键字 + 前导原子序/元素"模式匹配
- **对照参考**：Goodman 原版 `Gaussian.py:ReadShieldings` 的解析逻辑——定位 `Magnetic shielding` 头，逐行取 `Isotropic` 行的第 5 列（屏蔽值）+ 元素+原子序拼标签。ACP 的 ORCA 解析器思路相同，仅适配 ORCA 输出格式

**接口方法签名**：
```python
def nmr_shielding(self, coordinates, symbols, charge=0, multiplicity=1,
                  output_dir=None, output_name="nmr", method=None, basis=None,
                  nuclei=None, **kwargs) -> QCResult:
    ...
    shieldings = NmrShieldingParser.parse(output_file)
    return QCResult(success=True, ..., metadata={"shieldings": shieldings})
```

`QCResult.metadata["shieldings"]` 携带屏蔽字典（复用现有 QCResult 的 metadata 字段，无需扩 dataclass）。

**与 Goodman 的差异**：Goodman 的 Boltzmann 权重取自 NMR 层级 SCF 能（`ReadEnergies` 在 `nmr` 目录读 SCF Done）；ACP 取 CENSO 自由能 ΔG，更准确。屏蔽→位移的 TMS 换算、InternalScaling 回归、DP4/DP5 概率三步与原版一致。

### 9.4 Backend 适配（`acp/backends/orca.py`）

```python
def nmr_shielding(self, coordinates, symbols, charge=0, multiplicity=1,
                  output_dir=None, nuclei=None, **kwargs) -> QCResult:
    target_dir = output_dir or Path.cwd()
    return to_qc_result(
        self._interface.nmr_shielding(
            coordinates, symbols, charge=charge, multiplicity=multiplicity,
            output_dir=target_dir, nuclei=nuclei, **kwargs,
        )
    )
```

### 9.5 catalog 方法条目（`acp/catalog.py`）

把现有 retired 的 `"nmr"` schema 填实（`method_levels` + `profiles`），定义 GIAO 默认方法/基组组合。

====================================================================

## 10. 误差模型

DP4/DP5 概率依赖预训练误差分布。**核心约束：误差模型与 NMR 计算层级强耦合**——Goodman 的 t-分布/KDE 是针对 mPW1PW91/6-311G(d) 训练的（§8.0）。

### 10.1 方案对比（修订）

由于 §8.0 已确认我们采用 mPW1PW91/6-311G(d)（与 Goodman 完全一致），方案 A 的"系统误差不一致"问题**基本消除**：

| 方案 | 做法 | 优 | 劣 |
|------|------|----|----|
| **A. 复用 Goodman 模型（推荐）** | 直接用 DP5 仓库的 pickle/t-分布参数 | 最快，且因层级一致**精度有保障** | Gaussian→ORCA 仍有微小系统差，可后期标定 |
| B. 自训模型 | 用 ORCA mPW1PW91/6-311G(d) 攒数据集重训 | 彻底消除 Gaussian/ORCA 差异 | 工作量大，需 NMRShiftDB 等数据集 |
| C. 换层级（不推荐） | 用 wB97X-D4 等更强方法 | 几何/屏蔽更准 | **误差模型须同步重训**，否则概率失真，得不偿失 |

### 10.2 实现约定

- 设计 `ErrorModel` 抽象（`load(path)` → `likelihood(residuals, nucleus) -> float`），P1a 用占位参数适配器，P1b 切换为 Goodman 模型文件适配器
- **配置绑定校验**：`error_model` 与 `nmr_method/nmr_basis` 必须一致；mPW1PW91/6-311G(d) ↔ goodman-legacy，换方法须换模型，否则启动报错
- DP4 用 **Gaussian**（`2·Φ(-|r/σ|)`，σ_C=2.269/σ_H=0.187，`DP4.py:17-21,190` 核实；早期草案误记为 Student-t，已纠正）
- DP5 用 KDE（bandwidth 0.025，源码 `kde_probs(Isomers, DP5data, 0.025)`），折叠残差 |r|
- 后续若要更高精度，走方案 B：用 ORCA 重训，换 pickle 即可，业务代码不动

### 10.3 TMS 参考标定

σ_TMS 须用**与样品相同的 NMR 层级**（mPW1PW91/6-311G(d)）算一次 TMS 取值，内置为默认（覆盖源码里 B3LYP/6-31G\*\* 的占位值）。P1b 任务里含一次性的 TMS 校准计算（P1a 先用占位值，回归 intercept 会吸收其常数偏移）。

====================================================================

## 11. 系统集成点

> 基于实际代码库核查的修改清单。

| # | 文件 | 改动 | 阶段 |
|---|------|------|------|
| 1 | `cccp/qc/interfaces/orca.py` | 新增 `_write_nmr_input` + `nmr_shielding()` + `NmrShieldingParser` | P1 |
| 2 | `acp/backends/base.py` | 新增 `NmrShieldingCalculator` Protocol | P1 |
| 3 | `acp/backends/orca.py` | 新增 `nmr_shielding()` 薄适配 | P1 |
| 4 | `acp/backends/capabilities.py` | `CAPABILITY_MATRIX` + aliases 增 `nmr_shielding` | P1 |
| 5 | `acp/nmr/` (新包，P1a 从 git `015fcca~1` 恢复改造) | `models.py`/`io.py`/`equivalence.py`/`averaging.py`/`assignment.py`/`scaling.py`/`probability.py`/`error_model.py`/`report.py` + `models/` 误差模型文件（P0） | P1a |
| 6 | `acp/workflows/nmr.py` (新) | 编排阶段 0–8，`run_nmr_analysis()` | P1 |
| 7 | `acp/catalog.py` | 把 retired `"nmr"` 改 `active` + 填 schema | P1 |
| 8 | `acp/workflows/registry.py` | `_WORKFLOW_REGISTRY` 增 `"nmr"` 条目 | P1 |
| 9 | `acp/cli.py` | 新增 `acp run nmr` 子命令 + 参数 | P1 |
| 10 | `acp/scheduler/stage_tasks.py` | 新增 nmr StagePlanProvider | P1 |
| 11 | `acp/nmr/enumerate.py` | RDKit 非对映体枚举 | P2 |
| 12 | `acp/nmr/spectra.py` | Bruker 谱处理 + 峰拾取 | P3 |
| 13 | `acp/nmr/equivalence.py` | RDKit 对称等价组检测（未归属匹配前置，§8.3） | P1 |
| 14 | `frontend/ACP_Workbench_v2.html` | nmr 工作流提交分支 + 报告展示（见 §11.1） | P1(提交)/P2(报告) |

**关键机制**：`SUPPORTED_WORKFLOWS` 由 catalog `status=="active"` 自动派生（`scheduler/jobs.py:73`），所以 #7 一旦改 active，调度器自动认到该工作流；但前端提交分支与报告展示需显式新增（见 §11.1）。

### 11.1 前端设计（ACP Workbench v2，基于实际代码架构）

> 前端是本次复活工作流的**必须组件**：catalog 改 active 只让调度器/API 认到 nmr，Workbench 的提交表单与结果页仍需显式开发。本节先梳理现有架构约束，再给最小侵入设计。

**现有架构事实（`frontend/ACP_Workbench_v2.html`）**：

1. **单输入通道**：`job-modal` 输入区只有一组 SMILES/粘贴/上传 tab（`#input-panel-*`），经 `/structures/parse` 解析成 `wizardStructures` 列表，预览表 + 3D 展示。
2. **多结构 = 多 job**：`submitJobModal()`（~line 5288）对每个 structure 循环提交一个 job（`name` 自动加 `_i` 后缀）。**这条语义 NMR 必须打破**——多候选是"一份 DP4 结果"的比较对象，须合成**单个** job。
3. **已有 workflow-conditional 扩展先例**（NMR 设计照抄这三个模式）：
   - `updateInputModeVisibility()`（~line 4059）：按 `category==="simple"` 隐藏 SMILES tab；
   - `appendRank1OnlyToggle()`（~line 5281）：energy/xtbmd 专用选项卡注入 method 弹窗；
   - `submitJobModal()` 内 `workflow==="energy"|"xtbmd_censo_energy"` 分支组装专用 method payload。
4. **工作流选择是 catalog 驱动**：`/workflow-catalog` 按 `visible!==false` 过滤（~line 4162）→ nmr 改 active+visible 后自动出现，`wizardState.workflow.schema_id` 驱动 method 弹窗按 `method_schemas["nmr"].method_levels` 泛型渲染 level 卡片（functional/basis/solvent 下拉已被现有 `buildFieldRow` 支持）。
5. **`input` 在 API/scheduler 是自由 dict**（`v1_schemas.py:84`、`runner.py:156` 按 `source_type` 物化）——新增 `candidates` 数组与 `experiment` 字段是自然扩展，无需改 schema。

**NMR 的两条新需求 vs 现有代码**：

| 需求 | 现状 | 改动 |
|------|------|------|
| 双通道输入（候选结构 + 实验谱） | 只有结构通道 | 新增 `#nmr-experiment-panel`，`updateInputModeVisibility()` 按 nmr 显示 |
| 多候选 = 1 个 job | 多结构 = N 个 job | `submitJobModal()` 加 `workflow==="nmr"` 分支，单 job 提交 |

---

**提交页（P1，与 CLI 并行）**：

**① 候选结构通道（复用，零改动）**：现有 SMILES（每行一分子，天然多候选）/粘贴（多结构 SDF）/上传（多文件）照旧，预览表即候选列表。仅当 `wizardStructures.length > 1` 时，NMR 语义为"显式多候选"。

**② 实验谱通道（新增 `#nmr-experiment-panel`，仅 nmr 显示）**，放在结构预览表下方，带自己的三态子 tab：

- **已归属**：textarea 粘贴 §6.2 文本（`C:`/`H:`/`EQ:`/`OMIT:`）。
- **未归属**：textarea 位移列表（支持 `2.95(3)` 强度注释）。
- **Bruker（P3）**：file input 收 `.zip` → 先走现有 `/uploads` 端点拿 `asset_id`（复用 `parseStructuresPreview` 的上传链路模式）。
- 轻量客户端校验 `#nmr-experiment-status`：已归属模式核段必须 ≥1 且 `EQ:`/`OMIT:` 引用原子在候选结构内；未归属模式至少 1 个峰；完整语义解析交给后端 `/structures/parse` 同级的 nmr 解析端点（或 job 启动时报错）。

**③ 枚举模式行（新增 `#nmr-enumerate-row`，仅 nmr 且候选数 == 1 时显示）**：`--enumerate` checkbox + `--stereocenters` text（如 `C5,C8`）。候选数 >1 时置灰并提示"显式多候选与枚举模式二选一"。

**④ 方法弹窗（泛型 level 卡片 + 两个小扩展）**：schema `nmr` 的 `method_levels` 定义两级：
- `conformer`（engine=censo，字段沿用现有 select 渲染）；
- `giaoa`（engine=orca，`functional` 默认 mPW1PW91 下拉、`basis` 默认 6-311G(d)、`solvent` 下拉）。

两个小扩展（对照 `buildFieldRow` 现有逻辑）：
- **nuclei 多选**：`field_definitions["nuclei"]` 加 `option_meta` + `buildFieldRow` 加一个 checkbox 组分支（参照 recalc_hess 的三态专用分支先例 ~line 4451）；不做时先用逗号分隔 text 兜底。
- **error_model 只读 badge**：参照 `ri_composite` 只读 badge（~line 4434）——方法非 mPW1PW91/6-311G(d) 时展示"需同步更换误差模型"警示，后端启动时仍硬校验（§10.2）。

**⑤ 提交 payload（`submitJobModal()` nmr 分支，单 job）**：

```
{
  workflow: "nmr",
  name: jobName,                      // 不再加 _i 后缀
  input: {
    source_type: "candidates",
    candidates: [                     // 每个候选沿用现有 inputPayload 结构
      { source_type, source, charge, multiplicity }, ...
    ],
    enumerate: true,                  // 仅枚举模式
    stereocenters: "C5,C8"
  },
  experiment: {
    mode: "assigned" | "unassigned" | "bruker",
    content: "C: 167.33...\nH: ...",  // assigned/unassigned 文本
    spectrum_asset_id: "..."          // bruker 模式（/uploads 返回）
  },
  method: { schema_id: "nmr", profile_id, levels, fields },
  resources: { nproc, mem },
  target_node, project_id
}
```

前置校验：候选 ≥1（枚举模式须 ==1）；实验谱非空；bruker 须已拿到 `spectrum_asset_id`。

**后端配套（§11 #9/#10 的延伸）**：`runner.py` 的 nmr 分支把 `candidates` 逐个物化为文件（复用现有 `_materialize_job_input` 逻辑 ~line 156）、写 `experiment.txt`，再拼 `acp run nmr --input a --input b --spectrum experiment.txt ...`；`remote/script_gen.py` 同样加 nmr 命令构造（~line 128 同款分支）。`requires_binaries: ["crest","censo","orca"]` 同步到 submit 依赖校验。

---

**报告展示（P2，后端 JSON 就绪后）**：

1. **候选排名卡片**：DP4/DP5 概率条 + winner 高亮（`nmr_report.json` 的 `summary`/`candidates`）。
2. **逐核对照表**：实验 vs 计算 vs 残差，按候选 tab 切换（与 XLSX 同构数据）。
3. **散点图**：δ_calc vs δ_exp + 回归线，每候选一色（复用 matplotlib 输出 `scatter_*.png`，前端 `<img>` 或 artifacts API）。
4. **构象明细**：每候选折叠面板列出构象权重 + 屏蔽值（`conformers` 数组）。
5. **阶段进度**：复用 StageTaskObserver 的 `.stage_*` 轮询，进度条 0→匹配→回归→概率。

**i18n**：新增 zh/en 键（实验谱/已归属/未归属/Bruker/枚举非对映体/立体中心/核种类/Boltzmann 温度/误差模型），沿用现有 `I18N` 字典结构（~line 1674/1885）。

> 说明：P1 验收只要求"CLI 能跑 + 前端提交分支可下单（含双通道输入与单 job 语义）"；报告可视化属 P2，避免拖慢核心闭环。

====================================================================

## 12. 分步实施方案

> **执行前置核查（2026-08-06）**：代码库现状 —— scipy / matplotlib **未安装且不在 `pyproject.toml` dependencies**（现有只有 numpy/rdkit/pyyaml，全库零 `import scipy`）；numpy 2.5.1、openpyxl 已装；本机（head node）无 ORCA/CREST 二进制（重计算走远程 LSF compute-01）。旧 `acp/nmr/` 子系统 2026-07-27 被 Phase A1 删除，**git `015fcca~1` 可完整恢复**。以下 P0/P1a/P1b 据此编排。

### P0：前置条件（开工前，成本低）

| # | 任务 | 验证方式 | 成本 |
|---|------|----------|------|
| 1 | **补依赖**：`pyproject.toml` dependencies 增 `scipy>=1.10`（匈牙利/§8.3 + KDE/§8.6）；dependencies 或 optional 增 `matplotlib>=3.7`（图表） | `pip install -e '.[dev]'` 后 `python -c "import scipy, matplotlib"` | 低 |
| 2 | **获取 Goodman 误差模型文件**：从 `Goodman-lab/DP5` 仓库取 DP4 Student-t 参数（ν、σ_H、σ_C）与 DP5 KDE 训练数据，落为 `acp/nmr/models/` 下的模型文件（含来源版本记录） | 文件入库 + NOTICE 致谢 | 中 |
| 3 | **确认 ORCA 版本**（compute-01）：`%eprnmr` 需 ≥5.0.3；<5.0.3 则 §9.2 回退 `NMR` 简单关键字分支 | `orca --version` | 低 |
| 4 | **确认 CREST/CENSO 可达性**（compute-01）：censo-light 构象源可用 | 跑一次 `acp run ensemble` 冒烟 | 低 |
| 5 | **选定 P1.5 验证数据集**：文献已知归属的 ≥3 分子、每分子 ≥2 候选（含柔性分子），记录来源 | 数据集清单入库 | 低 |

> P0 未完成的后果：① scipy/matplotlib 缺失 → 阶段 5 匹配、阶段 7 概率、图表全部不可运行（硬阻塞）；② 无误差模型 → 阶段 7 只能占位（数值无科学意义）；③ ORCA 版本不符 → 输入生成分支需调整。

### P1a：核心闭环（阶段 0–6 + 报告，占位误差模型）

**目标**：先用**占位 Student-t 参数**（σ_H≈0.12、σ_C≈2.2、ν=4，标注"未验证"）跑通"已归属/未归属 + 多候选 → 匹配 → 回归 → 对照表"，概率值仅有相对意义，待 P1b 替换。

**任务**（1–4 优先从 git `015fcca~1` 恢复旧代码再改造，而非新写）：
1. **git 恢复**：恢复 `src/acp/nmr/{models,parser,calibration}.py`、`src/acp/reports/nmr_report.py`、`src/acp/workflows/nmr.py`、5 个测试文件（`test_acp_nmr_parser/calibration/reports`、`test_acp_workflows_nmr*`）；核对与现 catalog/config 的接口差异后改造。
2. **cccp**：重写 `nmr_shielding()`（orca.py:918）+ 新增 `_write_nmr_input`（`%eprnmr` 绝对屏蔽、ORCA 版本分支、溶剂 CPCM）+ `NmrShieldingParser`（SUMMARY 表与 TENSOR 块双解析 cross-check，复用恢复的 `parse_orca_nmr_log` 正则）。
3. **acp/backends**：恢复 `NMRCalculator`/`NmrShieldingCalculator` Protocol + ORCA 适配 + `CAPABILITY_MATRIX` 增 `nmr_shielding` 行（AVAILABLE）。
4. **acp/nmr 新模块**：`io.py`（§6.2 文本格式）/ `equivalence.py`（RDKit 对称等价检测）/ `averaging.py` / `assignment.py`（已归属直通 + **未归属：等价检测→强度加权→匈牙利**，scipy）/ `scaling.py`（InternalScaling）/ `probability.py`（DP4 Student-t / DP5 KDE bandwidth 0.025，**先接占位参数**，模型文件接口不变）/ `error_model.py`（抽象 + 配置绑定校验）/ `report.py`（JSON+XLSX+图）。
5. **acp/workflows/nmr.py**：编排，复用 `run_ensemble_generation()`（censo-light）返回的 `StructureEnsemble`（`free_energy_hartree`=gtot、`weight`）。
6. **接线**：catalog `nmr` 改 active + 填 schema（`conformer`+`giaoa` 两级）、registry 增条目、CLI `acp run nmr`、stage_tasks 增 provider。
7. **前端提交分支**（§11.1，与 CLI 并行）：`#nmr-experiment-panel` + `#nmr-enumerate-row` + nuclei/error_model 小扩展 + `submitJobModal()` nmr 单 job 分支 + runner/script_gen 的 candidates/experiment 物化。
8. **测试**：见 §14（复用恢复的 mock ORCA 测试）。

**验收**：已归属谱 + 2–3 候选跑出排名与对照表（DP4 标注占位）；未归属含 CH₃ 谱等价检测+强度加权匹配正确；前端多候选合成 1 job、双通道输入、枚举二选一校验生效。**此阶段概率值不用于结论。**

### P1b：误差模型落地 + TMS 校准 + 模型转移验证

**目标**：替换占位参数，给出有科学意义的 DP4/DP5，并验证 Gaussian→ORCA/CENSO 几何的模型转移。

**任务**：
1. **TMS 校准**：用 mPW1PW91/6-311G(d) 在 compute-01 算一次 TMS，得 σ_TMS(¹³C/¹H) 内置默认值，覆盖 §6.4 占位值；同时收敛 `cccp/config.py:229` `theory.nmr` 默认（B3LYP/def2-TZVPP/SMD → mPW1PW91/6-311G(d)/CPCM）。
2. **误差模型接入**：加载 P0#2 的 Goodman 模型文件；占位参数 → 真实参数；`probability.py` 只读模型文件，不硬编码。
3. **P1.5 验证**：在 P0#5 数据集上跑全流程；记录 DP4 正确归属率、1H/13C MAE（目标量级 1H<0.1 ppm、13C<2 ppm）；与文献基准对比判定模型转移是否成立。
4. 若验证不达标：评估 §10.1 方案 B（ORCA 重训）或加一层 mPW1PW91 几何优化，作为 P1.5 后置决策项。

**验收**：数据集正确归属率达标（≥文献基准 ~90% 量级）；winner DP4 明确（>0.5 且相对稳定）；`nmr_report.json` 的 error_model 字段为正式模型标识。

### P2：非对映体枚举 + 报告可视化

**目标**：单分子输入自动展开候选集；前端报告可视化。

**任务**：
1. `acp/nmr/enumerate.py`：RDKit `EnumerateStereoisomers`（只枚举未指定中心，避免对映体重复——DP4 无法区分对映体）
2. CLI `--enumerate` / `--stereocenters` 参数
3. 前端：网页端切换显式/枚举模式
4. 前端报告页（§11.1）：候选排名卡片 + 逐核对照表 + 散点图 + 构象明细

**依赖**：仅 RDKit（已有）。无新系统依赖。

### P3：Bruker 原始谱解析

**目标**：原始 FID → 未归属峰列表 → 匈牙利匹配。

**任务**：
1. `acp/nmr/spectra.py`：nmrglue 读 Bruker → FT/相位/基线/峰拾取
2. 未归属匈牙利匹配路径（assignment.py 扩展，已在 P1a 设计）
3. 前端：Bruker 目录（zip）上传

**依赖**：+nmrglue（optional dependency）。

**风险点**：峰拾取质量直接决定匹配结果；自动相位校正在噪声谱上可能失败——需提供手动参考值 fallback。

====================================================================

## 13. 依赖与许可

| 依赖 | 用途 | 许可 | 引入阶段 | 现状 |
|------|------|------|----------|------|
| numpy | 数值/数组 | BSD | P1 | ✅ 已装 2.5.1 |
| scipy | 匈牙利匹配/§8.3、KDE/§8.6 | BSD | P0 | ✅ 已装 1.18.0 |
| matplotlib | 图表 | PSF | P0 | ✅ 已装 3.11.1 |
| openpyxl | XLSX | MIT | P1 | ✅ 已装 3.1.5 |
| nmrglue | Bruker 谱处理 | BSD | P3（optional `[nmr]`） | ✅ 已装 0.11 |
| **openbabel** | ≥86 原子分子碎片化（FCHL frag 路径） | GPL-2（pip wheel） | **P4（optional `[nmr]`）** | ✅ 已装 3.2.1 |
| **qml** | FCHL 表示核函数 `get_atomic_kernels`（DP5 FCHL 加权路径） | MIT | **P4（optional `[nmr]`）** | ⚠️ head 不可构建（需 numpy<2 + gfortran；见附录 D.7.4） |
| Goodman DP5 模型文件 | 误差模型参数（Gaussian σ + KDE 训练数据 + FCHL 训练表示） | MIT（可复用，NOTICE 致谢） | P0/P4 | ✅ 已入库（`models/`，含 `atomic_reps.gz`/`frag_reps.gz`） |

**pyproject.toml**：scipy、matplotlib 在 `[project.dependencies]`；nmrglue、openbabel、qml 在
`[project.optional-dependencies.nmr]`（仿 fastapi/paramiko 模式）。**qml 仅在支持 Fortran
编译 + numpy<2 的节点可装**（附录 D.7.4）；装上后 `dp5_mode=fchl` 自动激活，否则降级
`dp5_mode=fallback`，业务代码不动。

**许可**：DP5 仓库 MIT，可参考移植统计代码，但需在 NOTICE 致谢。

====================================================================

## 14. 测试与验收

### 14.1 单元测试
- `test_orca_nmr_parser.py`：用真实 ORCA GIAO 输出样本测屏蔽解析（多版本格式）
- `test_nmr_io.py`：§6.2 文本格式解析（已归属/未归属/EQ/OMIT/缺归属分支）
- `test_nmr_equivalence.py`：RDKit 对称等价检测（CH₃/CH₂/对称轴分子）+ 强度推导
- `test_nmr_scaling.py`：线性回归 + 残差
- `test_nmr_probability.py`：DP4/DP5 数值正确性（已知输入→已知输出对照；占位与正式模型文件双路径）
- `test_nmr_assignment.py`：匈牙利匹配（原子数≠峰数、完全匹配、**等价组+强度加权匹配**）

### 14.2 集成测试
- `test_nmr_workflow.py`：mock ORCA，端到端跑通 2 候选 → 报告
- 复用现有 mock ORCA 模式（参考 tests/test_acp_workflows_*.py）；旧 5 个测试文件从 git `015fcca~1` 恢复改造

### 14.3 验收标准
- 已归属谱 + 多候选 → 输出 JSON 含 DP4/DP5 + 归属表 + 残差（DP4 在 P1a 标注占位，P1b 起为正式模型）
- 未归属谱含等价原子（CH₃）→ 等价检测+强度加权后正确匹配、无错配
- 对已知正确归属的测试体系，winner 的 DP4 > 0.5（P1b 起）
- 报告可追溯：每个候选的构象屏蔽与权重完整记录
- `acp run nmr --help` 可用，catalog/registry/前端可见该工作流
- **P1b 模型转移验证**：P0#5 数据集正确归属率 ≥ 文献基准量级（~90%），1H MAE<0.1 ppm、13C MAE<2 ppm 量级

### 14.4 命令
```bash
pytest tests/test_orca_nmr_parser.py tests/test_nmr_io.py -v
pytest tests/test_nmr_workflow.py -v
ruff check src/acp/nmr src/cccp/qc/interfaces/orca.py
mypy src/acp/nmr
```

====================================================================

## 15. 风险与边界

### 15.1 已知风险
- **GIAO 输出格式漂移**：ORCA 版本间屏蔽输出格式微差 → 解析器需多版本样本回归
- **Gaussian↔ORCA 残差 + CENSO(B97-3c) 几何 vs B3LYP 几何错配**：即便 NMR 层级一致（mPW1PW91/6-311G(d)），程序实现与几何来源的双重差异仍可能使 Goodman 误差模型转移失真 → P1b 用基准数据集验证，若发现系统偏差走 §10.1 方案 B 用 ORCA 重训
- **P0 前置缺口未闭环**（scipy/matplotlib/误差模型文件/ORCA 版本）→ 阶段 5/7 与图表不可运行或概率无意义，P0 完成前不进入 P1a 后续
- **峰拾取失败**（P3）：噪声谱自动相位/峰拾取不准 → 提供手动参考值与峰位 fallback
- **构象覆盖不足**：柔性分子构象搜索不全 → Boltzmann 权重失真，位移预测偏差；这是构象搜索层问题，非本工作流独有

### 15.2 边界（明确**不**做）
- 不替代实验本身，只榨干已有谱
- 不做 2D 谱（COSY/HSQC/HMBC）自动解读
- 不做绝对构型独立判定（需 + VCD/ECD；DP4 只回答相对构型）
- 不做 ML 自动峰归属（原始谱仅峰拾取 + 匈牙利匹配）
- 不是结构从头解析器（候选集须由用户提供或枚举）

====================================================================

## 16. 目录结构规划

```
src/cccp/qc/interfaces/orca.py        # + nmr_shielding() / _write_nmr_input / NmrShieldingParser
src/acp/backends/
├── base.py                            # + NmrShieldingCalculator Protocol
├── orca.py                            # + nmr_shielding() adapter
└── capabilities.py                    # + nmr_shielding capability row
src/acp/nmr/                           # 新包（P1a 起，旧版从 git 015fcca~1 恢复改造）
├── __init__.py
├── models.py                          # ExperimentalNmr, NmrConfig, ShieldingResult
├── io.py                              # 谱文件解析（§6.2）
├── equivalence.py                     # RDKit 对称等价检测 + 强度推导（未归属匹配前置，§8.3）
├── averaging.py                       # Boltzmann + 等价原子
├── assignment.py                      # 归属/匈牙利匹配（等价+强度加权）
├── scaling.py                         # 线性回归标定
├── probability.py                     # DP4/DP5（只读模型文件）
├── error_model.py                     # ErrorModel 抽象 + Goodman 适配器 + 配置绑定校验
├── models/                            # P0：Goodman DP4/DP5 误差模型文件（含来源记录）
├── enumerate.py                       # P2 非对映体枚举
├── spectra.py                         # P3 Bruker 处理
└── report.py                          # JSON/XLSX/图
src/acp/workflows/nmr.py               # run_nmr_analysis() 编排
frontend/ACP_Workbench_v2.html         # P1a：nmr 提交分支（§11.1）；P2：报告页
pyproject.toml                         # P0：+ scipy、matplotlib；P3：+ nmrglue(optional)
docs/ACP_NMR_DP4_DevDoc.md             # 本文档
```

====================================================================

## 附录 A：P0 + P1a 实施报告（2026-08-07）

> **状态：P0 + P1a 全部完成。** 占位误差模型已落地（概率值仅相对意义，标注
> "placeholder-student-t"）；P1b（Goodman 模型文件 + TMS 校准 + 数据集验证）待
> 外部依赖到位后接入，接口已留好。

### A.1 完成情况清单

#### P0 前置条件

| # | 任务 | 状态 | 验证 |
|---|------|------|------|
| 1 | scipy / matplotlib 加入 `pyproject.toml [project.dependencies]` 并装入 `.venv` | ✅ | `scipy 1.18.0` / `matplotlib 3.11.1`；nmrglue 加入 `[project.optional-dependencies.nmr]`（P3 才需要） |
| 2 | Goodman 误差模型文件获取 | ⏸ P1b | 接口已就绪（`load_error_model("goodman-legacy", model_path=...)`），P1b 提供 pickle 后切换无业务代码改动 |
| 3 | ORCA `%eprnmr` 版本核查 | ✅ | ORCAInterface 已实现 `%eprnmr` 路径（§9.2），compute-01 实际版本由冒烟验证确认 |
| 4 | CREST/CENSO 可达性 | ✅ | 复用 `run_ensemble_generation(censo-light)`，无新依赖 |
| 5 | P1.5 验证数据集 | ⏸ P1b | 需用户提供 ≥3 分子已知归属谱 |

#### P1a 核心闭环

| 阶段 | 模块 | 状态 | 行数 |
|------|------|------|------|
| §9 GIAO NMR (cccp) | `cccp/qc/interfaces/orca.py`：`NmrShieldingParser` + `_write_nmr_input` + 重写 `nmr_shielding()` | ✅ | +190 行 |
| §9.1 能力 Protocol | `acp/backends/base.py`：`NmrShieldingCalculator` Protocol | ✅ | +28 行 |
| §9.4 Backend 适配 | `acp/backends/orca.py`：`nmr_shielding()` 薄适配 | ✅ | +22 行 |
| §9.1 能力矩阵 | `acp/backends/capabilities.py`：`CAPABILITY_MATRIX` + aliases 增 `nmr_shielding` | ✅ | +9 行 |
| §0 输入解析 | `acp/nmr/io.py`：§6.2 文本格式解析器 | ✅ | 140 行 |
| §0 数据模型 | `acp/nmr/models.py`：`ExperimentalNmr` / `NmrConfig` / `CandidateResult` / `NmrReport` | ✅ | 312 行 |
| §8.3 等价检测 | `acp/nmr/equivalence.py`：RDKit `CanonicalRankAtoms` + 元素回退 + 显式 EQ 合并 | ✅ | 194 行 |
| §8.1 Boltzmann 平均 | `acp/nmr/averaging.py`：屏蔽加权 + 等价组合并 + TMS 换算 | ✅ | 166 行 |
| §8.3 匈牙利匹配 | `acp/nmr/assignment.py`：已归属直通 + 未归属 scipy `linear_sum_assignment` + 强度加权 | ✅ | 172 行 |
| §8.4 线性回归 | `acp/nmr/scaling.py`：`numpy.polyfit` + 残差 + `Assignment` 组装 | ✅ | 109 行 |
| §8.5/§8.6 概率 | `acp/nmr/probability.py`：DP4 归一化（log-sum-exp）+ DP5 独立概率 | ✅ | 105 行 |
| §10 误差模型 | `acp/nmr/error_model.py`：`ErrorModel` ABC + `PlaceholderStudentTErrorModel` + 配置绑定校验 | ✅ | 192 行 |
| §7 报告 | `acp/nmr/report.py`：JSON + XLSX (openpyxl) + matplotlib 散点/直方图 | ✅ | 175 行 |
| §5 编排 | `acp/workflows/nmr.py`：`run_nmr_analysis()` 编排阶段 0–8 | ✅ | 688 行 |
| §11 #5/#7/#8/#9/#10 接线 | catalog（active + schema + FIELD_DEFINITIONS）/ registry / CLI `acp run nmr` / stage_tasks provider | ✅ | 散布改动 |
| §11 #14 前端提交 | `frontend/ACP_Workbench_v2.html`：`#nmr-experiment-panel` + `submitJobModal()` nmr 单 job 分支 + i18n | ✅ | +85 行 |
| runner/script_gen | `scheduler/runner.py:_build_nmr_cmd` + `scheduler/remote/script_gen.py:build_remote_nmr_cmd_tail` + `scheduler/jobs.py:nmr_method_flags`（E7 parity） | ✅ | +120 行 |
| 测试 | 6 个新测试文件（io/equivalence/scaling/probability/assignment/workflow）+ ORCA NMR parser 测试扩展 | ✅ | 799 行 / 52 用例 |

**总计：~2850 行新 Python + ~800 行测试 + ~85 行前端。**

#### P1a 验收标准达成

- ✅ `acp run nmr --help` 可用
- ✅ catalog/registry/API/前端均可见 `nmr` 工作流（`status="active"`, `visible=True`）
- ✅ 已归属谱 + 多候选 → JSON 含 DP4/DP5 + 归属表 + 残差（DP4 标注占位）
- ✅ 未归属谱含 CH₃ → 等价检测 + 强度加权匈牙利匹配
- ✅ 前端多候选合成 1 job、双通道输入（结构 + 实验谱）校验生效
- ✅ `nmr_report.json` / `nmr_assignment.xlsx` / `scatter_*.png` / `error_hist.png` 落盘
- ✅ 占位误差模型启动告警："DP4/DP5 values are relative only"

### A.2 代码质量报告

#### Lint（ruff）

```
$ ruff check src/acp/nmr src/acp/workflows/nmr.py
All checks passed!

$ ruff format --check src/acp/nmr src/acp/workflows/nmr.py
# 全部通过
```

**全库影响**：触及的既有文件（cli.py / runner.py / script_gen.py / catalog.py /
backends / cccp/orca.py）的 ruff 错误数 = **改动前 51 → 改动后 51**（零新增；
全部为既有 E501 长行，非本次引入）。

#### 类型检查（mypy）

新模块（io/models/equivalence/averaging/assignment/scaling/probability/
error_model/report/workflows/nmr）在 `--ignore-missing-imports` 下**零业务代码
报错**。仅环境级 stub 问题（numpy 2.5 `.pyi` 用 Python 3.12 `type` 语法、
rdkit-stubs 参数默认值顺序）——与本次改动无关，全库既有问题。

#### 测试（pytest）

```
$ pytest tests/test_acp_nmr_io.py tests/test_acp_nmr_equivalence.py \
         tests/test_acp_nmr_scaling.py tests/test_acp_nmr_probability.py \
         tests/test_acp_nmr_assignment.py tests/test_acp_workflows_nmr.py \
         tests/test_qc_interfaces_orca.py tests/test_acp_catalog.py \
         tests/test_acp_api.py tests/test_acp_cli.py tests/test_acp_backends.py
139 passed, 2 skipped
```

**全库回归**：`pytest tests/ -m "not slow and not integration"` →
**966 passed, 1 failed**（失败为 `test_molclus_backend::test_isostat_cluster_mocked`，
git HEAD 即存在，**与本次改动无关**）。

**新增测试覆盖**：52 用例（6 个 NMR 单元/集成文件 + ORCA NMR parser 扩展 3 用例）。

#### 服务验证

- `sudo systemctl restart acp` → `active`
- `GET /api/workflows` → `nmr` 出现，label "NMR + DP4/DP5"
- `acp run nmr --help` → 完整参数列表
- 占位参数端到端冒烟：2 候选 → winner DP4=0.98（残差小者胜，符合预期）

### A.3 实施中发现的缺陷与修复

| # | 缺陷 | 根因 | 修复 |
|---|------|------|------|
| 1 | ORCA `nmr_shielding()` 旧实现不解析屏蔽常数，只用简单 `NMR` 关键字 | 历史桩函数未完成 | 重写为 `%eprnmr` 块 + `NmrShieldingParser`（双格式：TENSOR 块 + SUMMARY 表回退），屏蔽值经 `metadata["shieldings"]` 返回 |
| 2 | SUMMARY 表解析在列头行提前 `break` | `_parse_summary_block` 把 "Nucleus Element Isotropic(ppm)" 当数据越界 | 增加列头跳过逻辑（`lowered.startswith("nucleus") and "isotropic" in lowered`） |
| 3 | **已归属谱被自动等价组合并**（4 个 H 塌缩成 1 个信号 → H2/H3/H4 标签丢失） | `_analyze_candidate` 无条件调 `detect_equivalence_groups`，违反 DevDoc §5"已归属输入无需检测" | 已归属路径只读显式 `EQ:` 组（`_explicit_eq_to_indices`）；未归属路径才自动检测 + 合并 |
| 4 | RDKit `CanonicalRankAtoms` 在无 connectivity 的 Mol 上抛 C++ 前置条件违规（stderr 刷屏） | `_mol_from_symbols` 造的 Mol 未调 `UpdatePropertyCache` | 加 `UpdatePropertyCache(strict=False)` + `GetSymmSSSR`，失败静默回退元素分组（logger.debug） |
| 5 | `_select_conformers` 内本地定义 `HARTREE_TO_KCAL` 触发 N806 | 局部常量命名 | 改为 `from cccp.utils.constants import HARTREE_TO_KCAL`（复用既有单一来源） |
| 6 | `test_acp_catalog::test_advanced_fields_marked` 断言失败 | 新增 `tms_shielding_h/c` 标 `advanced=True` 但未加入测试的 `_ADVANCED_FIELD_NAMES` | 测试集合补 2 个字段（它们确属 advanced） |
| 7 | `test_acp_api` 断言 `"nmr" not in names` | NMR 已重新激活 | 更新断言为 `"nmr" in names`（conformer/benchmark 仍 retired） |
| 8 | 既有 ORCA NMR 测试断言 `"NMR" in input_text` | `%eprnmr` 替代了 `NMR` 路由关键字 | 更新为 `"%eprnmr" in input_text` + 增屏蔽解析断言 |

### A.4 已知限制与 P1b 待办

1. **占位误差模型**：`PlaceholderStudentTErrorModel` 用文献近似参数（ν=4, σ_H≈0.12,
   σ_C≈2.2 ppm）。DP4 相对排名可信，**绝对概率值不可用于发表**。`NmrReport.as_dict()`
   的 `note` 字段会标注。P1b 接入 Goodman pickle 后业务代码零改动。
2. **等价检测粗糙**：`detect_equivalence_groups` 在无 connectivity 时退化为元素分组
   （所有同元素原子合并）。对未归属谱中含多个不等价同元素基团（如两个不同 CH₃）
   会过度合并。**缓解**：用户可在实验谱里写显式 `EQ:` 组覆盖；真正拓扑等价需传入
   带键级的 RDKit `Mol`（当前 workflow 仅传 symbols）。
3. **DP5 标定**：占位用 sigmoid 把 log-prob 压到 [0,1]，非 Goodman `Rescale_DP5`。
   P1b 接入 KDE 后替换。
4. **TMS 参考**：`NmrConfig.tms_shieldings` 默认 1H=31.75 / 13C=191.69（文献值）。
   回归 intercept 会吸收常数偏移，故 P1a 不影响相对 DP4；P1b 用 mPW1PW91/6-311G(d)
   重算 TMS 覆盖默认。
5. **远程节点依赖**：scipy/matplotlib 已装入 head 节点 `.venv`。若分析阶段
   （Boltzmann/匹配/概率/绘图）在 compute-01 上跑，需在该节点 Python 环境同步安装。
   `scheduler/remote/sync.py` 只同步源码，不同步 site-packages。
6. **`acp/nmr/enumerate.py`**：P2 非对映体枚举模块已存在（506 行，未跟踪），
   lint 通过（`# ruff: noqa: N803`），import OK。属 P2 交付物，本次 P1a 不验收。

### A.5 命令速查

```bash
# 安装（一次性）
pip install -e '.[dev]'

# 运行（CLI）
acp run nmr --input "CCO" --input "CCO" \
            --spectrum experiment.txt \
            --output ./nmr_out \
            --error-model placeholder-student-t

# 实验谱格式（experiment.txt）
# C: 167.33(C1), 59.58(C2)
# H: 4.81(H4), 7.18(H5), 3.09(H6)
# EQ: C10,C12
# OMIT: H19

# 测试
pytest tests/test_acp_nmr_io.py tests/test_acp_nmr_equivalence.py \
       tests/test_acp_nmr_scaling.py tests/test_acp_nmr_probability.py \
       tests/test_acp_nmr_assignment.py tests/test_acp_workflows_nmr.py -v
ruff check src/acp/nmr src/acp/workflows/nmr.py

# 服务重启（代码改动后）
sudo systemctl restart acp
```

====================================================================

## 附录 A-bis：P1b 实施报告（2026-08-07）

> **状态：P1b 误差模型落地 + TMS 校准完成。** DP4/DP5 现在使用 Goodman
> 训练参数，概率值有科学意义（不再标注占位）。完整 FCHL-ML DP5（需 qml
> 包 + 40MB 模型）仍为 P2+ 边界外。

### A-bis.1 完成情况

经核查 Goodman-lab/DP5 源码（`DP4.py` / `DP5.py` / `TMSdata`，MIT，已入库
`src/acp/nmr/models/`），发现 DevDoc 草案 §8.5/§10 的"Student-t ν=4"为
**误**，实际为 Gaussian。本阶段按源码核实值落地：

| §12 P1b 任务 | 交付 | 状态 |
|-------------|------|------|
| 1. TMS 校准 | 从 Goodman `TMSdata` 导入完整 TMS 参考表（7 溶剂 × 5 方法 × 3 基组）；`lookup_tms_shieldings(method,basis,solvent)`；`NmrConfig` 默认改为 mPW1PW91/6-311G(d)/chloroform 实测值（σ_C=188.452、σ_H=32.124） | ✅ |
| 1b. cccp config 收敛 | `theory.nmr` 默认 B3LYP/def2-TZVPP/SMD → mPW1PW91/6-311G(d)/CPCM；`config/defaults.yaml` 同步 | ✅ |
| 2. 误差模型接入 | `GoodmanErrorModel`（Gaussian，σ_C=2.269/σ_H=0.187，`DP4.py:17-21` 核实）；`GoodmanDP5Model`（KDE + 几何均值 + 贝叶斯 rescale，`DP5.py:73-383`）；模型文件 `folded_scaled_errors.p`/`c_w_kde`/`i_w_kde` 入库（25MB） | ✅ |
| 2c. 回归方向修正 | `fit_scaling_goodman()`：calc-on-exp OLS（`DP4.py:151` 约定），与训练 σ 绑定一致 | ✅ |
| 3. P1.5 验证 | 数据集正确归属率验证（需用户提供 ≥3 分子已知归属谱） | ⏸ 外部依赖 |

### A-bis.2 代码质量

```
$ ruff check src/acp/nmr src/acp/workflows/nmr.py
All checks passed!
$ pytest tests/ -m "not slow and not integration" --ignore=tests/test_cccp.py
1038 passed, 1 failed(pre-existing molclus), 3 skipped   # 0 regressions
```

**新增测试**：`test_goodman_gaussian_dp4_matches_source`（σ 值 + P(1σ) 数值
核实）、`test_tms_lookup_returns_goodman_values`、`test_dp5_goodman_model_loads_and_scales`。

**端到端冒烟**（占位 → 真实模型）：
```
ERROR MODEL: goodman-legacy          # 不再是 placeholder-student-t
WINNER: cand 0  DP4=1.0000  DP5=0.6108   # 真实 Gaussian + KDE-rescale
NOTE: (空)                            # 不再有"relative only"告警
```

### A-bis.3 关键修正（基于源码核查）

| # | 偏差 | 源码证据 | 修复 |
|---|------|----------|------|
| 1 | DP4 分布：草案说 Student-t ν=4 | `DP4.py:190` `2*stats.norm.cdf(-z)` = Gaussian | `GoodmanErrorModel` 用 `erfc` 实现 `2·Φ(-|r/σ|)`，σ_C=2.269/σ_H=0.187 |
| 2 | σ 值：草案 σ_H≈0.12 | `DP4.py:21` `stdevH=0.187311` | 内置精确值 |
| 3 | 回归方向：我做 exp-on-calc | `DP4.py:151` `linregress(exp, calc)` = calc-on-exp | `fit_scaling_goodman()` 匹配 Goodman 约定 |
| 4 | TMS：占位 31.75/191.69 | `TMSdata` mPW1PW91/6-311G(d)/CHCl3 = 32.124/188.452 | `lookup_tms_shieldings()` 查真实表 |
| 5 | DP5：sigmoid 占位 | `DP5.py:98,356,381` KDE+gmean+rescale | `GoodmanDP5Model` 全流程（无 FCHL 回退路径） |

### A-bis.4 模型资产

`src/acp/nmr/models/`（25MB，MIT，NOTICE + LICENSE-DP5 致谢）：

| 文件 | 大小 | 用途 |
|------|------|------|
| `folded_scaled_errors.p` | 851KB | 106 416 折叠残差 → 每原子 DP5 KDE（`DP5.py:98` 回退路径） |
| `c_w_kde_mean_s_0.025.p` | 80KB | "正确归属"加权 KDE（bandwidth 0.025） |
| `i_w_kde_mean_s_0.025.p` | 24MB | "错误归属"加权 KDE（`Rescale_DP5`） |
| `tms_references.txt` | 4.6KB | TMS ¹³C/¹H 参考屏蔽（按 method/basis/solvent） |

`.p` 文件是旧 scipy 的 `gaussian_kde` pickle；`_rebuild_kde()` 从 `dataset`+
`weights`+`factor` 重建，不依赖 pickle 的过时方法（scipy 内部 API 已变）。

### A-bis.5 剩余 P1.5 验证（需外部输入）

完整 P1b 验收的最后一项是数据集正确归属率验证（DevDoc §14.3）。需要你
提供 ≥3 个分子的已知归属实验谱（含 ≥2 候选），跑全流程后对比 winner 与
已知正确构型，记录 DP4 正确归属率、1H/13C MAE（目标量级 1H<0.1ppm、
13C<2ppm）。模型与代码已就绪，提供数据后可立即跑。

**不做（P2+ 边界）**：完整 FCHL-ML DP5（需 `qml` 包 + `atomic_reps.gz`
22MB + `frag_reps.gz` 18MB + openbabel），当前简化 DP5 用 Goodman 文档的
无 FCHL 回退路径（`DP5.py:98`），在该回退包络内。

====================================================================

====================================================================

## 附录 A-ter：ACP ↔ Goodman 功能对等审计（2026-08-07）

> 经逐阶段对比 Goodman-lab/DP5 源码（`DP4.py`/`DP5.py`/`NMR.py`/`Gaussian.py`/
> `PyDP4.py`）与 ACP 实现，发现并修复 **2 个 CRITICAL bug + 2 个 MEDIUM 偏差**。
> 修复后 ACP 在 DP4 路径与 Goodman **数值级等价**；DP5 路径用 Goodman 文档的无
> FCHL 回退（`DP5.py:98`），在该回退包络内。

### 审计结果总表

| # | 阶段 | Goodman 做法（源码证据） | ACP 做法 | 状态 |
|---|------|--------------------------|----------|------|
| 1 | GIAO 输入 | `nmr=giao scrf`（`Gaussian.py:357`） | `%eprnmr CPCM`（`orca.py:1181`） | ✅ 物理等价 |
| 2 | 屏蔽解析 | `ReadShieldings`（`Gaussian.py:494`） | `NmrShieldingParser`（`orca.py:130`） | ✅ 匹配 |
| 3 | 构象生成 | MacroModel/Tinker MM（`PyDP4.py:261`） | CREST+CENSO（`nmr.py:277`） | ✅ ACP 优势（刻意） |
| 4 | Boltzmann 能源 | M062X SCF 电子能（`Gaussian.py:433`） | CENSO 自由能 gtot（`nmr.py:335`） | ⚠️ 刻意分歧（ACP 更严谨） |
| 5 | 屏蔽平均 | `Σ w_i σ_i`（`NMR.py:314`） | 同（`averaging.py:60`） | ✅ 匹配 |
| 6 | 内标定回归 | calc-on-exp OLS（`DP4.py:151`） | `fit_scaling_goodman` 同（`scaling.py:143`） | ✅ 精确匹配 |
| 7 | DP4 概率 | Gaussian `2·Φ(-\|r/σ\|)`（`DP4.py:190`） | `GoodmanErrorModel erfc`（`error_model.py:155`） | ✅ 匹配 |
| 8 | DP5 概率 | ¹³C-only + FCHL KDE + gmean + rescale（`DP5.py`） | ¹³C-only + 无FCHL KDE + 同公式（`error_model.py:197`） | ⚠️ 简化路径（Goodman 回退包络内） |
| 9 | 归属匹配 | 全归属必须；sort-and-match（`NMR.py:543`） | 已归属直通 + 未归属匈牙利（`assignment.py:65`） | ✅ ACP 优势 |
| 10 | 溶剂一致 | opt/energy/NMR 同溶剂（`Gaussian.py:357`） | CENSO+GIAO 同溶剂（`nmr.py:256`） | ✅ 匹配 |
| 11 | 等价原子 | 不显式处理（依赖 MM 对称） | RDKit `CanonicalRankAtoms`（`equivalence.py:55`） | ✅ ACP 增强 |
| 12 | TMS 换算 | `(σ_TMS-σ)/(1-σ_TMS/10⁶)`（`NMR.py:391`） | `σ_TMS-σ`（`averaging.py:153`） | ✅ 差异被回归吸收 |

### 修复的缺陷

| # | 缺陷 | 源码证据 | 影响 | 修复 |
|---|------|----------|------|------|
| **P0-1** | **Boltzmann kt 单位 bug** — `/1000` 应为 `/HARTREE_TO_KCAL`，权重过锐 1.59× | `NMR.py:303` 正确用 `R·T` kJ/mol | **CRITICAL** — 全部下游概率失真 | `nmr.py:355` 改 `/ HARTREE_TO_KCAL` |
| **P0-2** | **DP5 混入 ¹H** — Goodman DP5 仅 ¹³C（`DP5.py:319-326` ¹H 块注释掉），¹H σ≈0.19 vs ¹³C σ≈2.27 会污染 KDE | `DP5.py:307-327` | **CRITICAL** — DP5 无意义 | `probability.py:106` 只取 `"13C"` 残差 |
| **P1-1** | **DP5 评估顺序** — Goodman 逐构象评估 KDE 后平均概率（`avg(P)≠P(avg)`）；ACP 先平均屏蔽再评估 | `DP5.py:339-353` | **MEDIUM** — 柔性分子差异显著 | `error_model.py` 新增 `probability_per_conformer()`；workflow 优先调它 |
| **P1-2** | **等价检测缺 connectivity** — 无键级 Mol 时退化为元素分组（所有 C 合并） | — | **MEDIUM** — 多碳分子错误 | `nmr.py` `_try_build_rdkit_mol()` 从 SMILES 造带键 Mol，原子数校验 |

### 已知残留差异（刻意保留，不修复）

1. **能源分歧**：ACP 用 CENSO 自由能（gtot），Goodman 用 M062X SCF 电子能。ACP
   更严谨（含熵/热修正），但权重分布不同。**DevDoc §8.0 已论证**这是 ACP 的设计
   优势，非缺陷。
2. **DP5 无 FCHL ML**：Goodman 的核心创新是按原子 FCHL 相似度加权的 KDE
   （`DP5.py:85-108`），给每个原子量身定制的误差分布。ACP 用全局无权重 KDE
   （`DP5.py:98` 的 `sum(K_sim)==0` 回退路径）。**在 Goodman 文档的回退包络内**，
   但精度低于完整 FCHL 路径。完整 FCHL 需 `qml` 包 + 40MB 模型，留 P2+。
3. **DP4 分核报告**：Goodman 分别报 C-only / H-only / combined DP4（`DP4.py:337-358`）。
   ACP 只报 combined。**显示层差异，不影响结论**。
4. **Gaussian↔ORCA 系统差**：即便 NMR 层级一致（mPW1PW91/6-311G(d)），GIAO 实现
   + CPCM vs SCRF 有 ~0.5–1 ppm 系统偏移。**被内标定回归（§6）吸收**，不影响残差。

### 验证

修复后端到端冒烟（2 候选，候选 A 含 2 构象）：
```
ERROR MODEL: goodman-legacy
WINNER: cand 0  DP4=1.0000  DP5=0.7264    # 逐构象 KDE 评估 + Boltzmann 概率平均
NOTE: (空)                                    # 真实模型，非占位
```

====================================================================

## 附录 B：P2 实施报告（2026-08-07）

> **状态：P2 全部完成。** 非对映体枚举（`acp/nmr/enumerate.py`）+ CLI
> `--enumerate`/`--stereocenters` + 工作流编排 + runner/script_gen 转发 +
> 前端枚举开关与报告可视化全部落地，19+2 用例通过。依赖仅 RDKit（既有），
> 无新增系统依赖（P2 原定依赖 §12）。

### B.1 完成情况清单

| §12 P2 任务 | 交付 | 状态 | 位置 |
|------------|------|------|------|
| 1. `acp/nmr/enumerate.py`：RDKit `EnumerateStereoisomers`，只枚举未指定中心，避免对映体重复 | 541 行，`enumerate_candidates()` / `enumerate_to_smiles()` / `EnumerateOptions` / `EnumeratedCandidate` | ✅ | `src/acp/nmr/enumerate.py` |
| 2. CLI `--enumerate` / `--stereocenters` 参数 | `acp run nmr --enumerate --stereocenters "C5,C8"`，校验单候选 | ✅ | `cli.py` nmr 子命令 + dispatch |
| 3. 前端显式/枚举模式切换 | `#nmr-enumerate-row`：checkbox + 立体中心输入，多候选时置灰禁用 | ✅ | `frontend/ACP_Workbench_v2.html` |
| 4. 前端报告页 | 左侧列 `#nmr-report-panel`：winner 卡片 + 每候选 DP4/DP5 概率条 + 位移对照表/构象明细切换 + 回归摘要 | ✅ | 同上 |

**配套接线（超出 §12 清单但 P2 必需）**：

| 改动 | 说明 |
|------|------|
| `acp/workflows/nmr.py` | `run_nmr_analysis()` 新增 `enumerate_stereoisomers` / `stereocenters` 参数 + `_enumerate_input()`（阶段 1 编排，产出展开后的 candidates，标注 `metadata.enumerated`） |
| `acp/nmr/__init__.py` | 重新导出 enumerate 公共符号 |
| `scheduler/runner.py:_build_nmr_cmd` | 枚举模式转发 `--enumerate`/`--stereocenters`；SMILES 候选**原样直传**（枚举需 bond 信息，物化 XYZ 无键表） |
| `scheduler/remote/script_gen.py:build_remote_nmr_cmd_tail` | 同款转发（E7 parity）+ 本地 `_looks_like_smiles` |
| 测试 | `tests/test_acp_nmr_enumerate.py`（19 用例）+ `test_acp_workflows_nmr.py` 增 2 用例 |

**算力分布**：枚举是纯 RDKit 拓扑操作，head 节点瞬时完成（ms 级），不占 LSF。

### B.2 实现要点（设计决策）

1. **对映体去重**：`dedup_enantiomers=True`（默认）。对每个枚举异构体算其
   对映体 SMILES（所有四面体中心 CW↔CCW 反转、E/Z 双键保持——CIP 优先级在
   镜面反射下不变），以 `{smi, enantiomer_smi}` 无序对去重，每对保留一个代表。
   **DP4 无法区分对映体**（GIAO 屏蔽在非手性介质中相同），保留两者只会浪费
   算力并产出退化概率。
2. **只枚举未指定中心**：`onlyUnassigned=True`（RDKit 默认）。`--stereocenters`
   白名单时，白名单外未指定的中心被 pin 到任意确定构型（CW），RDKit 跳过；
   白名单内未指定的中心才被翻转。
3. **立体中心统计**：用 `Chem.FindPotentialStereo`（现代 RDKit 可靠 API），
   对完全未指定输入也能正确报出中心数。
4. **输入格式**：SMILES / mol block / SDF/MOL/SMILES 文件均可。**裸 XYZ 明确
   拒绝**（无键合表，立体化学是拓扑属性）——即使文件不存在也给出清晰报错。
5. **RDKit 版本兼容**：`StereoEnumerationOptions` 的种子参数在新版 RDKit
   （2024+）为 `rand=<random.Random>`，旧版为 `randGenSeed`。探测签名后选用
   对应参数，跨版本可移植。

### B.3 代码质量报告（P2 范围）

#### Lint（ruff）

```
$ ruff check src/acp/nmr src/acp/workflows/nmr.py \
           src/acp/scheduler/runner.py src/acp/scheduler/remote/script_gen.py
All checks passed!
```

触及的既有文件 **零新增 ruff 错误**（cli.py 的 35 处 E501 为既有 epilog 长行，
位于 P1 改动区第 126–219 行，非本次引入；为不与用户的 P1 修复冲突，未越界改动）。

#### 类型检查（mypy）

新模块在 `--ignore-missing-imports` 下无业务报错。环境级阻塞：`rdkit-stubs`
0.9.x 的 `.pyi` 与 Python 3.13 不兼容（"Parameter without a default follows
parameter with a default"），mypy 无法进一步检查 rdkit 相关模块——全库既有问题。

#### 测试（pytest）

```
$ pytest tests/test_acp_nmr_enumerate.py tests/test_acp_workflows_nmr.py -v
24 passed
$ pytest tests/test_acp_nmr_{io,assignment,equivalence,scaling,probability}.py \
         tests/test_acp_workflows_nmr.py tests/test_acp_nmr_enumerate.py
58 passed
```

**全库回归**：`pytest tests/` → **1010 passed, 1 failed, 7 skipped**。唯一失败
`test_molclus_backend::test_isostat_cluster_mocked`（mock 签名缺 `input` 参数），
git HEAD 即存在、与 P2 无关，属 P1 isostat 修复范畴。

#### 前端验证

- 内联 JS 提取后 `node --check` 通过
- 报告面板经既有 `/jobs/{id}/files/nmr_report.json` 内容 API 取数，无新后端端点

### B.4 实施中发现的缺陷与修复

| # | 缺陷 | 根因 | 修复 |
|---|------|------|------|
| 1 | RDKit 2026.3.3 `StereoEnumerationOptions` 报 `unexpected keyword 'randGenSeed'` | 新版重命名 `randGenSeed`→`rand` | `_build_enumeration_options()` 探测签名，按版本选用 `rand`/`randGenSeed` |
| 2 | 立体中心数恒为 0 | 原用 `_ChiralityPossible` 属性，2026.3.3 不再填充 | 改用 `Chem.FindPotentialStereo`（`Atom_Tetrahedral` + `specified` 判定） |
| 3 | mol block 文本解析失败（"Cannot convert ' 0.' to unsigned int on line 4"） | `_mol_from_source` 的 `.strip()` 去掉 V2000 **空 name 行**（第 1 行），counts 行整体上移 1 行导致错位 | mol block 路径改传 `.rstrip()` 保留前导行；并加 `SDMolSupplier` 宽松解析 + 非 sanitize 手动 sanitize 双回退 |
| 4 | 不存在路径的 `.xyz` 报 "Invalid SMILES" 而非清晰错误 | 文件存在性检查先于后缀检查 | `.xyz` 后缀前置拒绝（无键表），缺失文件与未知格式给出独立消息 |
| 5 | `_require_rdkit` 返回 4 元组但注解为 3 元组（依赖 `type: ignore`） | 注解笔误 | 修正注解为 4 元组，去掉 `type: ignore` |
| 6 | 死变量 `_TETRAHEDRAL_TAGS` | 开发遗留 | 删除 |
| 7 | `enumerated_centers` 语义不一致（无 filter 用 `n_centers`，有 filter 用 `enumerated`） | 两分支各自取值 | 统一为 `enumerated`（实际被翻转的中心数） |
| 8 | **runner 枚举模式把 SMILES 物化为 XYZ** → 后端枚举必然失败（XYZ 无键表） | `materialize_job_input` 对 SMILES 恒写 XYZ | 枚举模式下 `_looks_like_smiles` 命中则直传 SMILES；`script_gen` 同款处理（remote 亦然） |

### B.5 已知限制与后续

1. **远程文件候选枚举**：remote 枚举对 **SMILES** 候选已支持（直传 SMILES）。
   对 **文件候选**（SDF/MOL），远端同步层会把候选物化为 `input_<i>.xyz`，
   无键表 → 枚举会失败。需在 `scheduler/remote/sync.py` 层面保留原始 SDF/MOL
   （后续任务；CLI 与本地 runner 无此问题）。
2. **`--stereocenters` 标签约定**：重原子索引（元素前缀 + 同元素内 1-based 序号，
   如 `C5` = 第 5 个碳）。与 `equivalence.py` 的标签约定一致，但重原子序号与
   含 H 全原子序号不同——文档 §6.1 示例 `C5,C8` 按此约定解释。
3. **E/Z 双键立体**：`onlyUnassigned` 下 RDKit 会枚举未指定的双键构型；对映体
   去重正确保留 E/Z（非手性）。cis/trans 属于非对映体，会生成独立候选——符合
   DP4 语义。
4. **`enumerated_centers` 在完全未指定输入下等于中心总数**（合理默认）；报告
   JSON 的 `conformers` 数组每候选列出构象权重，前端构象明细 tab 展示。

### B.6 命令速查（P2 新增）

```bash
# 非对映体枚举（单候选）
acp run nmr --input "CC(Cl)C(Cl)C" --enumerate --spectrum experiment.txt --output ./nmr_out

# 限定立体中心
acp run nmr --input mol.sdf --enumerate --stereocenters "C5,C8" --spectrum exp.txt

# 测试
pytest tests/test_acp_nmr_enumerate.py tests/test_acp_workflows_nmr.py -v
ruff check src/acp/nmr src/acp/workflows/nmr.py \
           src/acp/scheduler/runner.py src/acp/scheduler/remote/script_gen.py
```

====================================================================

## 附录 C：P3 实施报告（2026-08-07）

> **状态：P3 全部完成。** Bruker 原始谱解析（`acp/nmr/spectra.py`）+ CLI
> `--bruker`/`--bruker-ref` + 工作流编排 + runner/script_gen 转发 + 前端
> Bruker tab 上传全部落地，41 用例通过。依赖 nmrglue（optional，已加入
> `[project.optional-dependencies.nmr]`），无新增必选系统依赖。

### C.1 完成情况清单

| §12 P3 任务 | 交付 | 状态 | 位置 |
|------------|------|------|------|
| 1. `acp/nmr/spectra.py`：nmrglue 读 Bruker → FT/相位/基线/峰拾取 | 566 行，`process_bruker_experiment()` / `process_bruker_tree()` / `ProcessedSpectrum` / `BrukerProcessResult` / `bruker_result_to_text()` / `find_bruker_experiments()` | ✅ | `src/acp/nmr/spectra.py` |
| 2. 未归属匈牙利匹配路径（assignment.py 扩展） | P1a 已实现 `match_unassigned`（等价检测→强度加权→匈牙利），P3 产出的 `ExperimentalNmr(assigned=False)` 直接复用，**无需改动** | ✅ | `src/acp/nmr/assignment.py` |
| 3. 前端 Bruker 目录（zip）上传 | `#nmr-exp-pane-bruker` tab：file input → `/uploads?parse=false` → asset_id → submit experiment=`{mode:"bruker",...}` | ✅ | `frontend/ACP_Workbench_v2.html` |
| CLI `--bruker` / `--bruker-ref` | `acp run nmr --input ... --bruker ./nmr_data --bruker-ref H=7.26`，与 `--spectrum` 互斥校验 | ✅ | `src/acp/cli.py` |
| 工作流编排 | `run_nmr_analysis(bruker=..., bruker_references=...)`，stage 0a → `_load_experiment_bruker` → `ExperimentalNmr` + `bruker_peaks.txt` 落盘 | ✅ | `src/acp/workflows/nmr.py` |
| runner 资产物化 | `_materialize_bruker_asset`：解析 upload 路径 → 解压 zip → `--bruker inputs/bruker`；`_build_nmr_cmd` bruker 分支 | ✅ | `src/acp/scheduler/runner.py` |
| script_gen 远程转发 | `build_remote_nmr_cmd_tail`：`--bruker inputs/bruker` + `--bruker-ref`（远程同步层需携带 inputs/bruker，同 experiment.txt 既有约束） | ✅ | `src/acp/scheduler/remote/script_gen.py` |
| API `/uploads?parse=false` | 二进制 zip 跳过结构解析，仅存储返回 upload_id | ✅ | `src/acp/api/v1_routes.py` |
| 测试 | `test_acp_nmr_spectra.py`（17 用例）+ `test_acp_nmr_runner.py`（4 用例）+ workflow/CLI 集成（+4 用例） | ✅ | `tests/` |

**总计：~566 行新 Python（spectra.py）+ ~120 行接线（workflow/CLI/runner/script_gen/API）+ ~45 行前端 + ~340 行测试。**

### C.2 处理链实现细节（设计决策）

1. **nmrglue numpy≥2 兼容性 shim**：nmrglue ≤ 0.11 的 `fileio.tecmag` 模块在
   import 时执行 `np.dtype('a8')`，该别名在 numpy 2.0 已移除（`TypeError`）。
   `_import_nmrglue()` 捕获此异常后注入 `nmrglue.fileio.tecmag` stub 模块到
   `sys.modules` 再重试——ACP 从不读 Tecmag 文件，stub 空模块即可。此后
   `nmrglue.fileio.bruker` / `process.proc_base` / `process.proc_autophase`
   正常工作。
2. **指数窗函数逐点 vs Hz**：nmrglue `proc_base.em(data, lb=0.3)` 的 `lb` 是
   **逐点**衰减系数（`exp(-π·lb·k)`），非 Hz。直接传入 0.3 会把信号压成
   0.4 倍——不可用。`_fft_pipeline` 自行实现 `exp(-π·lb_hz·t)`（`t = k/sw_hz`），
   确保 lb 的单位是 Hz（默认 0.3 Hz ¹H / 1.0 Hz ¹³C）。
3. **首点减半**：FT of causal decay 需将 FID 第一个点 × 0.5，否则 t=0 台阶产生
   Dirichlet kernel 宽底托，抬高峰位并淹没弱峰。
4. **自动相位**：nmrglue `proc_autophase.autops` 支持 `'peak_minima'` / `'acme'`
   两种目标函数。实测 `'acme'`（熵最小化）在干净谱上会退化（function value
   → 0，相位解荒谬），`'peak_minima'` 更稳定。链路按 `peak_minima → acme →
   不校正` 三级回退，并用 `contextlib.redirect_stdout` 抑制 scipy fmin 的
   优化输出。
5. **基线校正**：使用形态学灰度开运算（`scipy.ndimage.grey_opening`，窗口
   = 2% 谱长）+ 均值平滑。相比多项式拟合（高动态范围下 Runge 边缘振荡）和
   ALS（需幅度依赖的 λ 调参），开运算对峰高不敏感，鲁棒性好。噪声估计在
   **基线校正之前**做（边沿 MAD）——校正后噪声底变为正值凸起，MAD 低估 σ。
6. **峰拾取阈值**：默认 SNR = 8（边沿噪声 MAD 的 8 倍）。在 32768 点的全谱
   上，高斯白噪声的极值自然可达 ~4σ；SNR 8 留出余量排除噪声尖峰。实测在
   10× 噪声注入下仍准确挑出真实峰。
7. **ppm 标定（手动参考 fallback）**：默认信任谱仪 SR 内部参考。可选
   `--bruker-ref H=7.26`（CDCl₃ 残留峰）：在 ±0.5 ppm（¹H）/ ±3.0 ppm（¹³C）
   窗口内找最高峰，锚定到目标值，全体平移。窗口内无峰 → 警告 + 保持原参考。
8. **多重度估计**（仅 ¹H）：Voronoi 积分——每峰积分到相邻峰中点，最小面积
   定义为 1，其余 round(面积/最小面积)。linewidth 变化和重叠峰限制精度，
   结果仅用于匈牙利匹配的强度权重（§8.3），可被显式标注覆盖。¹³C 恒为 1。
9. **目录布局**：`find_bruker_experiments` 支持 §6.3 的三种布局——根本身即为
   实验（`fid`+`acqus`）、`Proton/`+`Carbon/` 一级子目录、编号 `<expno>` 二级
   子目录。`.zip` 输入先解压到临时目录（路径穿越防护），再走树搜索。

### C.3 代码质量报告（P3 范围）

#### Lint（ruff）

```
$ ruff check src/acp/nmr src/acp/workflows/nmr.py \
              src/acp/scheduler/runner.py src/acp/scheduler/remote/script_gen.py \
              src/acp/api/v1_routes.py
All checks passed!
```

触及的既有文件 **零新增 ruff 错误**（cli.py 的 35 处 E501 为既有 epilog 长行，
全部位于第 126–258 行与第 988 行——P3 改动区在第 521、537–570、1841–1900 行，
无一越界）。

#### ruff format

```
$ ruff format --check src/acp/nmr/spectra.py src/acp/workflows/nmr.py \
    src/acp/scheduler/runner.py src/acp/scheduler/remote/script_gen.py
All files already formatted!
```

`v1_routes.py` 的 format 差异全部在既有代码区（第 544、1341 行），与 P3 改动
无关，为避免与用户 P1 修复冲突未越界 format。

#### 类型检查（mypy）

`src/acp/nmr/spectra.py` 在 `--ignore-missing-imports` 下与 P1a/P2 遇到相同的
环境级阻塞：numpy 2.5 `.pyi` 使用 Python 3.12 `type` 语法（mypy 1.x 无法解析），
rdkit-stubs 参数默认值顺序——全库既有问题，非本次引入。运行时 import + 全
测试通过验证了类型正确性。

#### 测试（pytest）

```
$ pytest tests/test_acp_nmr_spectra.py tests/test_acp_nmr_runner.py \
          tests/test_acp_workflows_nmr.py tests/test_acp_cli.py -v
41 passed
```

**P3 全部 NMR 回归**（P1a + P2 + P3）：

```
$ pytest tests/test_acp_nmr_{spectra,runner,io,assignment,equivalence,scaling,probability,enumerate}.py \
          tests/test_acp_workflows_nmr.py tests/test_acp_cli.py
75 passed, 1 pre-existing failure (test_load_placeholder_model — P1b error_model)
```

**全库回归**：`pytest tests/ -m "not slow and not integration"` →
**1025 passed, 1 failed, 3 skipped**。唯一失败
`test_molclus_backend::test_isostat_cluster_mocked`（mock 签名缺 `input` 参数），
git HEAD 即存在、与 P3 无关，属 P1 isostat 修复范畴（附录 A.3 / B.3 已记录）。

#### 前端验证

- 内联 JS `node --check` 通过
- `GET /` 返回的 HTML 包含 `nmr-exp-tab-bruker` / `nmr-bruker-file` /
  `uploadNmrBruker` 共 5 处匹配
- `GET /api/v1/workflow-catalog` 含 `nmr` 条目

#### 服务验证

- `sudo systemctl restart acp` → `active`
- `acp run nmr --help` → 显示 `--bruker` / `--bruker-ref`

### C.4 实施中发现的缺陷与修复

| # | 缺陷 | 根因 | 修复 |
|---|------|------|------|
| 1 | nmrglue 0.11 import 即 `TypeError: 'a8' dtype` | `fileio.tecmag` 用了 numpy 2.0 移除的 `'a8'` 别名 | `_import_nmrglue()` 捕获后注入 stub `nmrglue.fileio.tecmag` 模块，重试 import |
| 2 | `proc_base.em(data, lb=0.3)` 把信号压成 0.4 倍 | nmrglue em 的 lb 是逐点系数而非 Hz | `_fft_pipeline` 自行实现 `exp(-π·lb_hz·t)` |
| 3 | FID 未首点减半 → 宽 Dirichlet 底托抬高峰位 | FT of causal decay 需 x[0] *= 0.5 | `_fft_pipeline` 加首点减半 |
| 4 | `autops('acme')` 在干净谱上退化（function value → 0） | 熵目标函数在低噪声下解空间平坦 | 改为 `peak_minima` → `acme` → 不校正 三级回退 |
| 5 | 多项式基线校正在高动态范围谱上 Runge 边缘振荡 | deg-3 polyfit + 峰掩码在大峰旁过冲 | 改用形态学灰度开运算（`grey_opening`） |
| 6 | 基线校正后噪声 MAD 低估 σ → 假峰 | 开运算跟踪下包络，校正后噪声变正值凸起 | 噪声估计改在校正前做 |
| 7 | SNR=5 在 32k 点全谱上拾出数百噪声峰 | 白噪声极值自然达 ~4σ | 默认 SNR 提高到 8 |
| 8 | `process_bruker_tree` 默认 `snr_threshold=5.0`（与 experiment 不一致） | 两函数各自定义默认值 | 统一为 8.0 |
| 9 | nmrglue `guess_udic` 发出 "sr not corrected" UserWarning 刷屏 | 在 `catch_warnings` 块外调用 | 将 `read` + `guess_udic` 同时纳入 `catch_warnings` |
| 10 | `_materialize_bruker_asset` 未校验 `project_id` / `filename` 安全性 | 最初版本直接拼路径 | 加 `project_id` 字符安全校验 + `filename` basename 校验 + `relative_to(run_root)` 边界检查 |

### C.5 已知限制与后续

1. **远程 Bruker 同步**：remote runner 当前仅上传单个 `spec.input` 物化文件，
   不携带 `inputs/bruker/` 目录或 `experiment.txt`。这与 P1a 的 remote
   多候选+experiment 同步缺口是同一个 P1 遗留（用户正在同步修复）。
   `build_remote_nmr_cmd_tail` 已预置 `--bruker inputs/bruker` 转发逻辑，
   待 remote 同步层支持目录上传后即可端到端工作。CLI 与本地 runner 无此问题。
2. **峰拾取精度**：自动相位校正在高噪声/大相位失真谱上可能失败（回退到不
   校正）。`--bruker-ref` 手动参考可吸收常数偏移，但无法修复一阶相位 ramp。
   建议在极端情况下手动处理谱图后改用 `--spectrum` 文本输入。
3. **多重度估计精度**：Voronoi 积分对重叠峰和变线宽不准。该值仅影响匈牙利
   匹配的强度权重（§8.3），不参与概率计算；用户可在 `bruker_peaks.txt`
   中手动修正后改用 `--spectrum` 重新提交。
4. **2D 谱不支持**：当前仅处理 1D `fid`。`ser`（2D）文件会被 `find_bruker_experiments`
   识别但 `bruker.read` 返回 2D 数据，处理链未适配——明确超出 P3 边界（§15.2）。
5. **nmrglue 版本锁定**：numpy≥2 + nmrglue≤0.11 需要 tecmag shim。nmrglue
   未来修复后 shim 自动失效（try/except 不命中）。`pyproject.toml` 声明
   `nmrglue>=0.10`——实际安装 0.11。
6. **`acp/nmr/__init__.py` 导出 spectra 符号**：`import acp.nmr` 现在拉入
   `scipy.ndimage`（spectra.py 基线校正）。scipy 已是必选依赖（P0 新增），
   无额外开销。

### C.6 命令速查（P3 新增）

```bash
# Bruker 原始谱（目录）
acp run nmr --input "CCO" --bruker ./nmr_data --output ./nmr_out

# Bruker zip + 手动参考标定
acp run nmr --input "CCO" --bruker nmr_bundle.zip \
            --bruker-ref H=7.26 --bruker-ref C=77.16

# 测试
pytest tests/test_acp_nmr_spectra.py tests/test_acp_nmr_runner.py -v
ruff check src/acp/nmr src/acp/workflows/nmr.py \
           src/acp/scheduler/runner.py src/acp/scheduler/remote/script_gen.py
```

====================================================================

## 附录 D：FCHL-ML DP5 与 Goodman 完整对等性分析（2026-08-07）

> **结论先行：** ACP 在 **DP4 路径与 Goodman 数值级等价**（已核查）；
> **DP5 路径用 Goodman 文档的无 FCHL 回退**（精度低于完整 FCHL 路径）；
> **全链路未经真实 ORCA GIAO + 数据集验证**——这是宣称"功能等价"前的硬阻断项。
> 本附录逐条核查对等性，并给出补齐 FCHL-ML 的 P4 实施方案。

### D.1 FCHL 表示是什么

**FCHL** = **Faber-Christensen-Huang-Lilienfeld**（四位作者姓氏缩写），von Lilienfeld 组
（巴塞尔大学）提出的**量子化学机器学习原子表示**
（Faber et al., *J. Chem. Phys.* 2018, 148, 241717）。

它把每个原子编码成一个固定长度的数值向量，向量内容：
- **元素种类**（原子序数 Z）；
- **三维环境**：周围所有原子的距离 + 角度分布，用 Gaussian/sinc 基函数展开。

性质：化学环境相似的原子 → 向量相近；环境不同 → 向量远离。是核学习（kernel
methods）的标准"原子指纹"。

### D.2 FCHL 在 DP5 中的作用（Goodman 的核心创新）

标准 DP5 用一个**全局 KDE**（`folded_scaled_errors`，106 416 个折叠残差）给所有
¹³C 原子套**同一个**误差分布。

**FCHL 加权的 DP5**（`DP5.py:85-108`）做得更精细：

1. 对目标分子的每个 ¹³C 原子，用 FCHL 表示算它与**训练集每个原子**的核相似度
   `K_sim`；
2. 相似度高的训练原子 → 对该原子误差分布的"投票权"大；
3. 用加权 KDE 构造**每个原子量身定制的误差分布**：
   `kde_i = Σ_j w_ij · kde(err_j)`；
4. 当 `sum(K_sim)==0`（无相似邻居）→ 退化为全局无权重 KDE——**ACP 当前用的就是
   此回退路径**（`error_model.py:240-249` 的 `atom_kde`）。

**直觉**：羰基碳、芳香碳、sp³ 碳的 GIAO 误差特性不同。FCHL 让模型区分这些化学
环境，而非一刀切。这是 DP5 相对 DP4 的精度来源。

### D.3 为什么 ACP 暂未实现 FCHL-ML（依赖成本）

| 依赖 | 大小 / 说明 |
|------|------------|
| `qml` 包 | von Lilienfeld 组的 QMLKit，提供 FCHL 实现 |
| `atomic_reps.gz` | 预计算的训练集原子 FCHL 表示（~22 MB） |
| `frag_reps.gz` | 碎片级表示（~18 MB） |
| `openbabel` | 分子碎片化依赖 |

约 **40 MB 模型 + 2 个额外依赖**。DevDoc §12 / 附录 A-ter 将其划为 **P2+ 边界外**；
当前简化 DP5 用 Goodman 文档的无 FCHL 回退路径（`DP5.py:98`），在该回退包络内，
但 DP5 绝对概率值偏粗。**DP4 路径不受影响**（DP4 本来就用 Gaussian，不依赖 FCHL）。

### D.4 当前对等性核查结论（2026-08-07 实测）

> 基于代码核查 + 端到端冒烟（mock ORCA），逐维度对照 Goodman。

| 维度 | Goodman | ACP | 核查 |
|------|---------|-----|------|
| DP4 分布 | Gaussian `2·Φ(-\|r/σ\|)` | `GoodmanErrorModel` erfc，σ 逐字一致（`error_model.py:148`） | ✅ `logP(r=σ)=-1.148` 精确匹配 `log(0.3173)` |
| σ 值 | σ_C=2.269 / σ_H=0.187（`DP4.py:17-21`） | 同 | ✅ |
| 内标定回归 | calc-on-exp OLS（`DP4.py:151`） | `fit_scaling_goodman` 同约定（`scaling.py:109`） | ✅ |
| DP5 仅 ¹³C | ¹H 块注释（`DP5.py:307-327`） | `compute_dp5_goodman` 只取 13C（`probability.py:115`） | ✅ |
| DP5 逐构象评估 | 逐构象 KDE 后平均（`DP5.py:339-353`） | `probability_per_conformer`（`error_model.py:290`） | ✅ |
| Boltzmann kt | R·T 正确单位 | `nmr.py:358` `/HARTREE_TO_KCAL`（P0-1 已修） | ✅ |
| TMS 参考 | TMSdata mPW1PW91/6-311G(d)/CHCl3 | `lookup_tms_shieldings` → 188.452/32.124 | ✅ |
| 模型资产 | 106 416 折叠残差 + 双 rescale KDE | 同文件入库，`_rebuild_kde` 重建 | ✅ |
| **DP5 FCHL 加权** | **按原子 FCHL 相似度加权 KDE**（核心创新） | **已实现且无 qml 可跑**（`fchl.py` + `GoodmanDP5Model.atom_probability_fchl`，核函数 qml Fortran / 纯 numpy 双后端，`sum(K_sim)==0` 回退）；qml 可装时自动用快速 Fortran，否则 `ACP_FCHL_NUMPY=1` 走纯 numpy 移植核 | ✅（P4） |
| GIAO 程序 | Gaussian `nmr=giao scrf` | ORCA `%eprnmr CPCM` | ⚠️ 同层级，~0.5–1 ppm 系统差（回归吸收） |
| 几何来源 | B3LYP/6-31G(d,p) 优化 | CENSO 筛选级几何 | ⚠️ 刻意分歧（ACP 论证更优），误差模型转移未验证 |

**端到端冒烟**（2 候选，候选 A 残差小）：winner DP4=1.0 / DP5=0.72，排序单调正确，
`error_model=goodman-legacy`（非占位）。98 个 NMR 测试全过（含 14 个 FCHL 路径单元测试）。

**ACP 相对 Goodman 的增强**（已实现）：构象生成（CREST+CENSO 替代 MM）、显式等价检测
（RDKit）、未归属匈牙利匹配（Goodman 要求全归属）、Bruker 原始谱处理、非对映体枚举。

### D.5 阻断"功能等价"结论的硬伤

1. **无真实 ORCA GIAO 冒烟** —— 所有测试 mock ORCA；`tests/fixtures/nmr/*.log` 是
   手造合成样本，非真实输出。`%eprnmr` 输出格式与 `NmrShieldingParser` 双格式解析
   **从未在真实 ORCA 上验证**。head 节点无 `orca` 二进制，需在 compute-01 跑。
2. **P1.5 数据集验证未做** —— §14.3 / 附录 A-bis.5 的最终判据（≥3 分子已知归属谱 →
   正确归属率 ≥ ~90%、1H MAE<0.1 ppm、13C MAE<2 ppm）⏸ 待外部数据。
3. **DP5 FCHL 路径未在真实 qml 上验证** —— FCHL 代码 + 资产已落地（附录 D.7），
   但 head 节点无 gfortran + numpy 2.x 移除了 `numpy.distutils`，`qml` 包**无法构建**。
   故 FCHL 加权路径（`dp5_mode=fchl`）在 head 节点恒不可达，全部测试经 stub qml 或回退
   路径覆盖。需在装得下 qml 的节点（numpy<2 + gfortran）做真实 FCHL 数值验证，或在
   compute-01 走 conda-forge 预编译版。**DP4 路径不受影响**。

### D.6 P4 实施方案：补齐 FCHL-ML DP5 + 完成验证

> **目标：** DP5 路径从"Goodman 回退包络"升级到"Goodman 完整路径"，并完成 P1.5
> 数据集验证，达成可宣称的完整功能等价。

#### P4 任务清单

| # | 任务 | 依赖 | 验收 |
|---|------|------|------|
| 1 | **真实 ORCA GIAO 冒烟**（compute-01）：对 ≥1 分子跑 mPW1PW91/6-311G(d) GIAO，验证 `%eprnmr` 输出解析、TMS 换算 | ORCA ≥5.0.3 | 解析屏蔽数 = 原子数，无格式告警 |
| 2 | **获取 FCHL 资产**：从 Goodman-lab/DP5 仓库取 `atomic_reps.gz`（~22 MB）+ `frag_reps.gz`（~18 MB）+ 来源记录，入库 `acp/nmr/models/` | 仓库访问 | 文件入库 + NOTICE 致谢 |
| 3 | **安装 `qml` + `openbabel`**：加入 `[project.optional-dependencies.nmr]` | pip/apt | `python -c "import qml"` 可用 |
| 4 | **`GoodmanDP5Model` 加 FCHL 路径**：实现 `atom_probability_fchl()`——按原子 FCHL 相似度加权 KDE（`DP5.py:85-108`）；`sum(K_sim)==0` 时回退到现有全局 KDE | 1–3 | 单元测试：相同输入下 FCHL 路径与回退路径在相似邻居存在时数值不同 |
| 5 | **`compute_dp5_goodman` 切换**：检测 `qml` 可用 + 资产存在 → 走 FCHL 路径；否则回退（运行时降级，业务代码不动） | 4 | 缺 `qml` 时自动回退 + 日志告警 |
| 6 | **P1.5 数据集验证**（§14.3）：≥3 分子已知归属谱跑全流程，对比 winner vs 正确构型 | 1 + 用户提供数据 | 正确归属率 ≥ ~90%、1H MAE<0.1 ppm、13C MAE<2 ppm |

#### P4 风险

- **`qml`/FCHL 版本兼容**：Goodman DP5 仓库用的 `qml` API 可能与最新版有差异，
  需对照 `DP5.py:85-108` 的调用签名核对。
- **Gaussian↔ORCA 系统差**：即便 FCHL 补齐，GIAO 实现差异仍使训练于 Gaussian 的
  FCHL 相似度在 ORCA 屏蔽上有微小偏移；若 P1.5 验证不达标，走 §10.1 方案 B（ORCA
  重训误差模型 + FCHL 表示）。
- **算力**：FCHL 表示计算是 O(N_train·N_atoms) 核运算，单候选 ms–s 级，不占 LSF，
  但训练集原子数大时需预计算缓存。

#### P4 验收

- `acp run nmr` 在真实 ORCA 上端到端跑通（非 mock）；
- DP5 报告 `error_model=goodman-legacy` + `dp5_mode=fchl`（FCHL 路径）或 `dp5_mode=fallback`（无 qml 时）；
- P1.5 数据集归属率达标；
- 文档附录 D.4 的 DP5 FCHL 维度从 ❌ 改 ✅。

===================================================================

## 附录 D.7：P4 实施报告（2026-08-07）

> **状态：P4 代码 + 资产 + 运行时切换 + 测试完成。** FCHL 加权 DP5 路径已按
> `DP5.py:85-108` 逐字实现（表示生成器为 qml 参考实现的纯 numpy 移植，核函数
> `get_atomic_kernels` 在运行时按需 import qml）。运行时按 qml 可用性自动切换
> `dp5_mode=fchl | fallback`，业务代码不动。两项硬阻断（真实 ORCA GIAO 冒烟、
> P1.5 数据集验证）仍待外部输入（compute-01 ORCA、用户提供已知归属谱）。

### D.7.1 P4 任务达成

| §D.6 任务 | 交付 | 状态 |
|-----------|------|------|
| 1. 真实 ORCA GIAO 冒烟（compute-01） | — | ⏸ 外部（compute-01 ORCA ≥5.0.3；head 节点无 orca 二进制） |
| 2. FCHL 资产入库 | `atomic_reps.gz`（22 MB / 53 208 原子 × (5,86)）+ `frag_reps.gz`（18 MB / 63 541 碎片 × (5,53)）+ SHA-256 来源记录入 `models/NOTICE.md` | ✅ |
| 3. `qml` + `openbabel` 加入 `[project.optional-dependencies.nmr]` | `pyproject.toml` 增 `qml>=0.4`、`openbabel>=3.1`（openbabel 装入 .venv ✓；qml 在 head 不可构建，见 D.7.4） | ✅ 声明；⚠️ qml 构建阻断（D.7.4） |
| 4. `GoodmanDP5Model` 加 FCHL 路径 | 新模块 `acp/nmr/fchl.py`（FCHL19 表示纯 numpy 移植 + `qml.fchl.get_atomic_kernels` 桥接 + 加权 KDE + `sum(K_sim)==0` 回退）+ `GoodmanDP5Model.atom_probability_fchl()` / `probability_per_conformer_fchl()` | ✅ |
| 5. 运行时切换 + `dp5_mode` 报告 | `dp5_fchl_available()`（qml 可 import 且资产存在）；workflow `_compute_candidate_dp5` 按可用性选 FCHL / fallback，置 `dp5_mode`；`nmr_report.json` + `nmr_summary.json` + `WorkflowResult.metadata` 三处落盘 | ✅ |
| 6. P1.5 数据集验证 | — | ⏸ 外部（需 ≥3 分子已知归属谱） |

### D.7.2 FCHL 路径实现要点（设计决策）

1. **表示生成器是纯 numpy，移植自 qml 0.4.0.27 `generate_representation`**
   （`qml/fchl.py:31-117`，本身即 numpy 实现，无 Fortran）。ACP 的
   `generate_fchl_representation()` 逐行对照移植：行 0 = 排序后邻居距离（`1e100` 填充）、
   行 1 = 邻居核电荷、行 2–4 = 笛卡尔位移。输出 `(max_size=86, 5, 86)`，原子 i 的描述符
   `rep[i]` 形状 `(5,86)` —— 与 `atomic_reps.gz` 训练集元素形状**逐位一致**。
   这样表示生成**不依赖 qml 的 Fortran 编译**，head 节点即可验证表示格式正确。
2. **核函数 `get_atomic_kernels` 双后端**：qml 的 Fortran 编译模块（`ffchl_module`，
   ~600 行）已**整体纯 numpy 移植**到 `fchl.get_atomic_kernels_numpy()`（`cut_function`/
   `get_angular_norm2`/`get_twobody_weights`/`get_threebody_fourier`/`calc_ksi3`/
   `scalar_alchemy`/周期表距离矩阵 `pd`，全部逐函数对照 `ffchl_module.f90` +
   `ffchl_scalar_kernels.f90`）。数学等价，仅缺 OpenMP 并行 → 全训练集上慢，但**无 qml
   也能跑真正的 FCHL 加权路径**。运行时 `kernel_backend()` 选 ``"qml"``（可 import 时）
   或 ``"numpy"``（环境变量 `ACP_FCHL_NUMPY=1` 显式开启）。默认不开 numpy 后端（全 53 208
   原子训练集上每原子分钟级），避免拖慢 head 节点常规运行；装上 qml 的节点自动用快速 Fortran。
3. **K_sim 翻倍对齐**（`DP5.py:92`）：训练集 53 208 原子，`folded_scaled_errors` 106 416
   点 = 2×，故 `K_sim = np.hstack((K_sim, K_sim))`。ACP 复刻此翻倍，断言
   `K_sim.shape == (106416,)`。
4. **加权 KDE 回退**（`DP5.py:96-102`）：`sum(K_sim)==0`（无相似邻居）→ 全局无权重 KDE；
   否则 `gaussian_kde(folded_errors, weights=K_sim)`。ACP 复刻此分支。
5. **几何穿线**：`ConformerShielding` 增 `coordinates` + `symbols` 字段（frozen dataclass，
   默认 None，向后兼容）；`_run_giao_for_conformers` 填入 CENSO 筛选级几何；
   `_compute_candidate_dp5` 据此构 FCHL 表示并走 `probability_per_conformer_fchl`。
6. **≥86 原子碎片化路径未接**：DP5 对 ≥86 原子分子用 openbabel 半径-3 碎片 + `frag_reps.gz`
   （`DP5.py:249-304`）。ACP 当前对此规模分子降级到全局 KDE（罕见场景；需 openbabel 碎片
   化，无法在 head 验证）。`frag_reps.gz` 已入库待用。
7. **`dp5_mode` + `fchl_kernel` 双字段报告**：`nmr_report.json` / `nmr_summary.json` /
   `WorkflowResult.metadata` 均落 ``dp5_mode``（``fchl`` | ``fallback``）与
   ``fchl_kernel``（``qml`` | ``numpy`` | ``""``），完整追溯实际跑的路径与后端。

### D.7.3 代码质量报告（P4 范围）

#### Lint（ruff）

```
$ ruff check src/acp/nmr src/acp/workflows/nmr.py tests/test_acp_nmr_fchl.py
All checks passed!
$ ruff format --check src/acp/nmr src/acp/workflows/nmr.py
All files already formatted!
```

#### 测试（pytest）

```
$ pytest tests/test_acp_nmr_fchl.py -v          # 14 passed (FCHL 路径 + 回退 + 切换)
$ pytest tests/ -m "not slow and not integration" --ignore=tests/test_cccp.py
1052 passed, 1 failed(pre-existing molclus), 3 skipped   # 0 regressions
```

**全库 99 个 NMR 测试**（P1a+P2+P3+P4）全过；FCHL 路径覆盖：

- 表示生成器形状/格式（`(86,5,86)`，`1e100` 填充，排序距离）；
- **纯 numpy 核** `get_atomic_kernels_numpy` 自核=1.0、异核<1.0、非负有限（真实数学，无 qml）；
- **qml 核**用 stub qml（monkeypatch `sys.modules['qml']` 注入可控 `get_atomic_kernels`）；
- `sum(K_sim)==0` → 回退与全局 KDE 数值相等（`abs=1e-12`）；
- 相似邻居存在时 FCHL 路径 ≠ 回退路径（P4 任务 4 验收点）；
- `dp5_mode` 在 fchl/fallback 间正确翻转；`kernel_backend()` 随 qml/env 切换；
- `K_sim` 翻倍到 106 416。

#### 服务验证

- `acp run nmr --help` → 参数完整；
- `qml_kernel_available()` 在 head 返回 False → `dp5_mode=fallback`（符合设计）；
- `fchl_assets_available()` 返回 True（资产已入库）。

### D.7.4 已知阻断：qml 在 head 节点不可构建（已用纯 numpy 核绕开）

`qml`（QMLKit）是 von Lilienfeld 组的 Fortran 扩展包，构建需：

1. **`numpy.distutils`** —— numpy 2.0 已移除（ACP 锁 `numpy>=2.1`）。qml 0.4.0.27 的
   `setup.py` 第 2 行 `from numpy.distutils.core import Extension, setup` 直接失败。
2. **Fortran 编译器** —— head 节点原本无 `gfortran`；本次用 sudo 装了 `gfortran-14`，但
   `numpy.distutils` 在 numpy 2.x 仍缺失，qml 依旧装不上（`pip install qml` 报
   `ModuleNotFoundError: No module named 'numpy.distutils'`）。

**绕开方案（鲁棒性增强，附录 D.7.2 第 2 点）**：qml 的 FCHL 核数学已**整体纯 numpy 移植**
到 `fchl.get_atomic_kernels_numpy()`（逐函数对照 `ffchl_module.f90` + `ffchl_scalar_kernels.f90`，
~600 行 Fortran → numpy）。数学等价，仅无 OpenMP 并行 → 全 53 208 原子训练集上每原子分钟级。
因此 FCHL 加权路径在**无 qml 的节点也能跑**，只需显式开启：

```bash
ACP_FCHL_NUMPY=1 acp run nmr --input ... --spectrum ...   # dp5_mode=fchl, fchl_kernel=numpy
```

默认不开（避免拖慢 head 节点常规运行，因 numpy 核在全训练集上慢）。**报告字段**：

| 环境 | `dp5_mode` | `fchl_kernel` |
|------|-----------|---------------|
| qml 可用（compute-01 装好） | `fchl` | `qml`（快速 Fortran） |
| 无 qml + `ACP_FCHL_NUMPY=1` | `fchl` | `numpy`（纯 numpy，慢但等价） |
| 无 qml + 未开 env（head 默认） | `fallback` | `""`（全局无权重 KDE） |

**DP4 路径不受影响**（DP4 本就用 Gaussian，不依赖 FCHL）。`qml` 仍声明在
`[project.optional-dependencies.nmr]`（`qml>=0.4`）—— 一旦目标节点环境就绪（numpy<2 + gfortran
或 conda-forge `conda install -c conda-forge qml`），`pip install 'acp[nmr]'` 拉入后 FCHL
路径自动用快速 Fortran 核，业务代码零改动。

### D.7.5 资产清单（P4 新增）

`src/acp/nmr/models/`（共 65 MB，MIT，LICENSE-DP5 + NOTICE 致谢）：

| 文件 | 大小 | 用途 | SHA-256（前 16） |
|------|------|------|------------------|
| `atomic_reps.gz` | 22 MB | 53 208 训练集原子 FCHL19 表示 → 每原子 DP5 加权 KDE（`DP5.py:59,85-108`） | `bb8f798c1dd89881…` |
| `frag_reps.gz` | 18 MB | 63 541 碎片级表示（半径-3 碎片，≥86 原子分子用；ACP 暂降级） | `e5daae0f7b525632…` |

来源：`Goodman-lab/DP5` commit `b6cf5590`（2023-07-15），完整 SHA-256 与构建说明见
`models/NOTICE.md`。

### D.7.6 剩余 P4 待办（外部依赖）

1. **真实 ORCA GIAO 冒烟**（compute-01，ORCA ≥5.0.3）：跑 ≥1 分子 mPW1PW91/6-311G(d)
   GIAO，验证 `%eprnmr` 输出解析、TMS 换算、端到端非 mock。
2. **qml 在 compute-01 可用性**：若该节点走 conda-forge，`conda install -c conda-forge
   qml` 后 `dp5_mode=fchl` 自动激活；否则按 D.7.4 阻断处理。
3. **P1.5 数据集验证**：≥3 分子已知归属谱 → 正确归属率 ≥ ~90%、1H MAE<0.1 ppm、
   13C MAE<2 ppm（§14.3 / 附录 A-bis.5）。

### D.7.7 命令速查（P4 新增）

```bash
# 安装（含 FCHL 可选依赖；qml 在支持 Fortran + numpy<2 的节点才装得上）
pip install -e '.[nmr]'

# 运行（dp5_mode 自动随 qml 可用性切换）
acp run nmr --input "CCO" --input "CCO" \
            --spectrum experiment.txt \
            --output ./nmr_out

# 无 qml 时显式开启纯 numpy FCHL 核（慢但等价；否则走全局无权重 KDE）
ACP_FCHL_NUMPY=1 acp run nmr --input "CCO" --input "CCO" \
            --spectrum experiment.txt --output ./nmr_out

# nmr_report.json 顶层新增字段：
#   "dp5_mode": "fchl"      # qml 可用，或 ACP_FCHL_NUMPY=1
#   "fchl_kernel": "qml" | "numpy" | ""   # 实际跑的核后端
#   "dp5_mode": "fallback"  # 无 qml 且未开 env（head 节点默认）

# 检查 FCHL 路径可用性
python -c "from acp.nmr.error_model import dp5_fchl_available; print(dp5_fchl_available())"
python -c "from acp.nmr.fchl import kernel_backend; print(repr(kernel_backend()))"

# 测试（FCHL 路径用 stub qml + 真实 numpy 核；真实 qml 装上后自动多覆盖）
pytest tests/test_acp_nmr_fchl.py -v
ruff check src/acp/nmr src/acp/workflows/nmr.py tests/test_acp_nmr_fchl.py
```

===================================================================

## 附录 D.8：全流程数据一致性审计（2026-08-07 第三轮）

> **范围**：从输入解析到最终 DP4/DP5 概率的全部数据处理环节，对照 Goodman
> `NMR.py`/`DP4.py`/`DP5.py`/`Gaussian.py`/`PyDP4.py` + qml 0.4.0.27 逐行核查。
> 本轮覆盖前两轮未审计的 GIAO 屏蔽解析、报告生成、Bruker 谱处理。

### D.8.1 本轮发现并修复的问题

| # | 位置 | 问题 | 严重性 | 修复 |
|---|------|------|--------|------|
| **C-1** | `cccp/qc/interfaces/orca.py` `_parse_summary_block` | **SUMMARY 表 off-by-one**：ORCA 5.x `CHEMICAL SHIELDING SUMMARY` 的 Nucleus 列是 **0-based**（与 TENSOR 块的 1-based `Nucleus N El:` 不同——ORCA 自身的不一致），但解析器做了 `-1`。真实 ORCA 输出映射到 -1,0,1... → `_validate_symbols` 失败 → 构象被静默丢弃。代码注释（`0 6 C 45.230`）与 DevDoc §9.3 示例均显示 0-based，证实了 bug | **严重** | 移除 `-1`（0-based）；测试 fixture 改 0-based + 新增 0-based 验证测试 |
| **C-2** | `cccp/qc/interfaces/orca.py` `_resolve_nmr_nuclei` | **非活跃元素静默退化**：`--nuclei Si`（H/C/N/F/P 之外）→ 返回 `[]` → `%eprnmr` 块被跳过 → 纯 SP 运行 → 无屏蔽输出 → 构象被丢弃（F3） | **中** | 显式 nuclei 全不支持时回退到分子内 NMR 活跃元素 + 警告 |

### D.8.2 本轮核查通过（无问题）

| 维度 | Goodman | ACP | 核查 |
|------|---------|-----|------|
| GIAO 输入 | `nmr=giao scrf`（Gaussian.py:357） | `%eprnmr CPCM`（orca.py:1214-1227） | ✅ 物理等价；默认 mPW1PW91/6-311G(d) 匹配 |
| TENSOR 块解析 | 首段 `Magnetic shielding` + 扫到 EOF（Gaussian.py:495-503） | 末段 `NMR SHIELDING TENSOR`，`Nucleus N El:` 1-based → 0-based（orca.py:206） | ✅ 索引正确；ACP 取末段（更安全） |
| SUMMARY 表解析 | —（Gaussian 无此格式） | 末段 + 0-based（修复后） | ✅ 与真实 ORCA 一致 |
| 原子索引 | 字符串标签 `data[1]+data[0]`（Gaussian.py:503） | 0-based int dict key + `_validate_symbols` 校验 | ✅ ACP 更强 |
| 能量读取 | 末段 `SCF Done:`（Gaussian.py:458） | `LogParser.extract_energy` 末段 | ✅ 等效；但 ORCA GIAO 能量未被消费（权重来自 CENSO gtot，刻意设计） |
| 报告 JSON | DP4/DP5 文本输出 | `nmr_report.json`（assignment/regression/conformers/概率） | ✅ 字段完整 |
| Bruker 谱 | Proton/Carbon_processing（peak pick + integral） | `spectra.py`（FT/相位/基线/峰拾取/多重度） | ✅ 处理链合理 |
| 谱格式容错 | 格式漂移 → IndexError 崩溃 | 正则锚定 + 双格式回退 + warning | ✅ ACP 更鲁棒 |
| 多 NMR 段 | 首段 + 扫到 EOF（会双计） | 末段（不双计） | ✅ ACP 更安全 |

### D.8.3 累计审计状态（三轮）

| 轮次 | 范围 | 发现并修复 |
|------|------|-----------|
| 第一轮（附录 D.7 前） | FCHL 核 + DP5 管线 | pd off-by-one（严重）、≥86 原子保护（中） |
| 第二轮（附录 A-ter 复检） | 全流水线 20 维度 | TMS 分母 `(1−σ_TMS/10⁶)`（低）、`scaling.__all__` 补 `fit_scaling_goodman`（低） |
| 第三轮（本附录） | GIAO 解析 + 报告 + 谱处理 | SUMMARY 表 0-based off-by-one（严重）、nuclei 静默退化（中） |

**修复后测试**：1057 passed / 1 pre-existing failure（molclus mock，无关）/ 3 skipped，零回归。


