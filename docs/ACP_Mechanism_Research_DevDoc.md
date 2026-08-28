# ACP 机理研究平台升级 — 开发文档 v2.0

> **⚠️ RETIRED（2026-08-28）** — 本文档为历史设计方案，`src/acp/mechanism/` 已在 refactor/calc-cleanup 分支删除。机理研究能力已拆分为独立工作流：PESsearch（路径搜索）+ BatchOptimize（批量优化）+ irc（端点验证）。本文档仅保留供历史参考。

**版本**: v2.0 · 2026-08-12
**状态**: 方案设计(未实施 — 本文件仅含设计,不含代码改动)
**配套可视化**: `ACP_Mechanism_Research_DevDoc.html`(核心架构变更速览)

---

## 目录

- [1. 文档定位与信息优先级](#1-文档定位与信息优先级)
- [2. 核心架构原则](#2-核心架构原则)
- [3. 现状盘点(代码审计结论)](#3-现状盘点代码审计结论)
- [4. 总体架构(三层)](#4-总体架构三层)
- [5. 核心数据模型](#5-核心数据模型)
- [6. Provider 契约层](#6-provider-契约层)
- [7. 阶段语义与 ACP 落点(顶层 6 阶段)](#7-阶段语义与-acp-落点顶层-6-阶段)
- [8. Quality Gate 体系(G0–G5)](#8-quality-gate-体系g0g5)
- [9. S3/S4:RefinementEngine + FidelityProfile](#9-s3s4refinementengine--fidelityprofile)
- [10. TS/INT Identity(泛化)](#10-tsint-identity泛化)
- [11. SR:反应网络 + FrontierQueue + EndpointMatcher](#11-sr反应网络--frontierqueue--endpointmatcher)
- [12. Checkpoint / Provenance / Resume](#12-checkpoint--provenance--resume)
- [13. CLI / Catalog / Scheduler / API / 前端 注册面](#13-cli--catalog--scheduler--api--前端-注册面)
- [14. 能力缺口清单(必须新增的底层代码)](#14-能力缺口清单必须新增的底层代码)
- [15. 里程碑 M0–M4](#15-里程碑-m0m4)
- [16. 验收标准(彻底可用化定义)](#16-验收标准彻底可用化定义)
- [附录 A:RPH v4.0.1 主线核实记录](#附录-arph-v401-主线核实记录)
- [附录 B:相对 v1.0 方案的修订对照表](#附录-b相对-v10-方案的修订对照表)

---

## 1. 文档定位与信息优先级

本方案建立在一个明确的信息优先级之上:

| 信息来源 | 权威级别 | 说明 |
|---|---|---|
| **本地 ACP 代码审计**(2026-08-12) | 权威 | 本仓库 `src/acp/mechanism/`、`src/acp/workflows/mechanism.py`、scheduler、api 等现状以本地代码为准。本地 ACP 已具备 guided-scan / rph-reverse / candidate_refine / ts_validate 等机制,不再是简单 placeholder |
| **RPH 当前 GitHub 主线(v4.0.1)** | 权威 | `github.com/PengYangchao0808/ReactionProfileHunter`,tag `v4.0.1`(commit `3abbaecdd0b3c8cad6c4106c6e3ea07b6071e437`),已 clone 逐文件核实(见附录 A)。RPH 的科学语义以该主线为准,不覆盖 ACP 现状 |

**核心判断**:

> ACP = 机理研究的 **Study / Network / Scheduling 平台**
> RPH = ACP 中一套经过 [4+3] 验证的 **Mechanism Method Provider**

而不是"把 RPH S0–S4 复制一份到 ACP"。这是后续能否真正支持多样化机理研究(未来 NEB/GSM/多自旋态路径)的关键。

---

## 2. 核心架构原则

**从 RPH 移植"科学 contract",不移植 [4+3] 假设。**

### 2.1 应当移植(进入 ACP core)

- CENSO-LITE ensemble philosophy(CREST/GFN2 采样 + B97-3c SP + xTB mRRHO)
- 全路径 method-consistent 能量精化(full-path SP,非单点)
- **seed ≠ stationary point**(SeedCandidate 统一 schema)
- **FidelityProfile**(immutable,驱动全部精度差异,engine 零分支)
- **RefinementEngine P0–P3**(Preflight / Primary / Rescue / Canonical)
- role-based warmup / initial Hessian 策略
- **有序救援矩阵**(8-cell,非笛卡尔积)
- attempt history + canonical winner(不删失败历史)
- TS mode identity(虚频模式对齐 RC)
- evidence-based INT identity(classify_int_v2 方向)
- versioned manifest + hash-based resume

### 2.2 不应当进入 ACP core(封装在 RPH Method Provider 内部)

- exactly two forming bonds(强制两根成键)
- product-only backward PEB(仅产物端逆向)
- mean forming-bond distance
- dipolar intermediate-specific thresholds
- RPH-specific knee/right-shift selector(`endpoint_knee_shift_midpoint_v1` 参数)
- [4+3]-specific atom mapping 假设

---

## 3. 现状盘点(代码审计结论)

### 3.1 当前 mechanism 流水线(9-stage 线性,`src/acp/workflows/mechanism.py` 914 行)

| Stage | 函数(行号) | 状态 | 真实缺口 |
|---|---|---|---|
| prepare_reaction | `stage_prepare_reaction` (272) | ✅ 已实现 | 仅角色标记 — 无构象/系综摄入 |
| reactant_optimize | `stage_reactant_optimize` (295) | ✅ 已实现 | 单构象 ORCA 优化,无系综循环 |
| product_optimize | `stage_product_optimize` (344) | ✅ 已实现 | 无产物时跳过,单构象 |
| path_search | `stage_path_search` (399) | ⚠️ 部分 | guided-scan / rph-reverse / direct-ts 存活;**endpoint-path 是 stub**(回落 guided-scan,strategies.py:237-239) |
| candidate_refine | `stage_candidate_refine` (458) | ⚠️ 部分 | 仅 Top-1 TS seed;救援只执行矩阵第一条(action[0],mechanism.py:552) |
| ts_optimize | `stage_ts_optimize` (587) | ✅ 已实现 | **无任何救援**;与 refine 同 fidelity |
| ts_validate | `stage_ts_validate` (633) | ⚠️ 部分 | 仅虚频计数门;**mode_match_score 未接线**;topology_sane 硬编码 True(676) |
| irc_validate | `stage_irc_validate` (679) | ⚠️ 部分 | 端点仅文件路径;**无端点→INT 转换**;final_geometries 从不填充 |
| energy_analysis | `stage_energy_analysis` (735) | ✅ 已实现 | 仅势垒;无热力学/无 SP 重评估 |

### 3.2 关键基础设施缺口(方案必须覆盖)

| # | 缺口 | 证据 |
|---|---|---|
| 1 | **S1 构象系综不存在**:mechanism 无任何 CREST/构象钩子,reactant/product 是单几何 ORCA opt | mechanism.py:295/344;grep 零 conformer 引用 |
| 2 | **跨工作流交接仅多帧 XYZ**:`ensemble.json` 只写不读,无 manifest 消费者 | energy.py:259(_is_multiframe_xyz),ensemble.py:141-166 |
| 3 | **"censo-lite"(GFN 级)预设不存在**:censo-light 是 B97-3c DFT;rcfile 硬编码 `prog = orca` | censo.py:115-144,295 |
| 4 | **rescue 关键词大部分死代码**:`ts_mode/mode_displacement/opt_level/irc_midpoint_reseed` 被 ORCA 接口静默丢弃 | orca.py:1210(仅 pop solvent/solvent_model/grid/scf/nproc) |
| 5 | **sp_kwargs() 从未被调用**:rph-s3 的 r2SCAN-3c SP 和 rph-s4 的 wB97M-V SP 是死代码 | presets.py:75-86,mechanism.py 全文件无调用 |
| 6 | **"irc" 不是能力名**:`require_backend("irc")` 会 ValueError;IRC 挂在 transition_state 下 | registry.py:20-39,capabilities.py:34-57 |
| 7 | **ORCA 版本检测缺失**:`_VERSION_FLAGS["orca"] = ()`,"ORCA 611" 无版本门控 | software.py:77 |
| 8 | **CLI/调度器奇偶断裂**:`mechanism_method_flags` 发射 `--scan-points/--irc-points`,但 CLI parser 无此二 flag → argparse 报错 | jobs.py:288-289 vs cli.py:893-988 |
| 9 | **IRC `InitHess Read` 无条件发出**但 .hess 不复制到 irc_validate 目录 | orca_ts.py:158-176 |
| 10 | **无多步/递归**:WorkflowRunner 严格线性,routes[0] 仅用第一条;无 INT→TS→INT 链 | workflow.py:86,mechanism.py:242 |
| 11 | **Top-3 TS seeds 只精化 Top-1**;INT seeds 选出后无下游消费 | candidates.py:21,mechanism.py:514 |
| 12 | **无 WAITING_REVIEW 状态 / 无 mechanism-studies API / 无网络可视化** | jobs.py:29-51,v1_routes.py 无此路由,前端 1159-1160 空壳 |

---

## 4. 总体架构(三层)

```
┌─────────────────────────────────────────────────────────────────┐
│  ACP Mechanism Study 层(平台语义:study/network/scheduling)      │
│  Study Orchestrator · ReactionNetwork(有向多重图)               │
│  ExplorationFrontier · DecisionPoint · QualityGate G0–G5        │
│  Study 级 checkpoint/provenance/resume · 报告(JSON)             │
├─────────────────────────────────────────────────────────────────┤
│  Provider 契约层(Protocol,统一 schema,可组合)                   │
│  EnsembleProvider · PathSearchStrategy · RefinementProvider     │
│  EndpointProvider · ThermochemistryProvider                     │
│  统一结果对象:StableStateEnsemble / PathResult /                │
│  SeedCandidate[] / RefinementManifest / EndpointMatchResult     │
├─────────────────────────────────────────────────────────────────┤
│  具体 Provider(可插拔,provenance 记录 provider+版本)            │
│  ACP GuidedScan · RPH(CENSO-LITE/ReversePEB/Refinement)         │
│  DirectTS · (未来) NEB / GSM / GoodVibes / pyGSM                │
└─────────────────────────────────────────────────────────────────┘
```

**与现有代码映射**:

- Study 层 = 新 Orchestrator(位于 `mechanism.py` 之上),沿用 `WorkflowState`(state.py:49)作为 stage checkpoint 基座;
- Provider 契约层 = 新 `acp/mechanism/providers/` 包,协议定义仿 `acp/backends/base.py` 的 capability Protocol 风格;
- 底层 QC 执行**不改动**现有 backends(`ORCABackend`/`XTBBackend`/`CrestBackend`,capabilities.py 矩阵保持)。

---

## 5. 核心数据模型

新建/重构于 `src/acp/mechanism/models.py`(现模型扩展)。**破坏性收敛**:现 `PathPoint/PathResult/PathCandidate`(models.py:87-189)、`MechanismRoute`(models.py:26)、`MechanismStudy`(models.py:192)被重构替换;`StructureEnsemble`(core/models.py:283)继续作为 StableStateEnsemble 载体。

### 5.1 顶层 Study 容器

```python
@dataclass
class MechanismStudy:                       # 现有模型(models.py:192)扩展,首次真正实例化
    study_id: str
    atom_identity_map: AtomIdentityMap      # 新:G0 gate 核心
    stable_states: list[StableState]        # 新:取代裸 StructureRecord
    routes: list[Route]                     # 现有 MechanismRoute 扩展
    stationary_points: list[StationaryPoint]
    elementary_steps: list[ElementaryStepEdge]
    network: ReactionNetwork                # 新:有向多重图
    frontier: ExplorationFrontier           # 新
    decision_points: list[DecisionPoint]    # 新:持久化人工门
    quality_gates: list[QualityGateResult]  # 新

@dataclass(frozen=True)
class AtomIdentityMap:                      # G0 gate 核心(建议 8)
    uid_to_structure_index: dict[str, int]  # {"a7": 6, "a12": 11} —— 可跨 conformer/优化/远程重编译
    mapping: dict[str, dict[str, int]]      # canonical SMILES ↔ geometry index 映射链
```

### 5.2 StationaryPointRequest(由 RPH StructureRequest adapter 而来)

```python
@dataclass
class StationaryPointRequest:
    id: str
    role: Literal["reactant", "product", "intermediate", "transition_state"]
    kind: Literal["minimum", "ts"]
    input_geometry: ArtifactRef
    coordinate_plan: ReactionCoordinatePlan | None
    fallback_geometries: list[ArtifactRef]
    source_stage: str
    charge: int
    multiplicity: int
    atom_mapping: AtomIdentityMap | None
    parent_state_id: str | None
    route_id: str | None
    ensemble_correction: ThermoCorrection | None   # S1 系综修正
    provenance: Provenance
```

### 5.3 CoordinateSpec 修订(不加 bond_stretch kind)

```python
@dataclass(frozen=True)
class CoordinateSpec:
    id: str
    kind: Literal["distance", "angle", "dihedral"]
    atom_refs: tuple[AtomRef, ...]
    action: Literal["drive", "freeze", "monitor"]
    start: float | None
    end: float | None
    weight: float = 1.0
    # GUI 语义(Form/Break/Rotate/Bend bond)→ 编译成 kind+action+方向,不进底层
```

### 5.4 统一 PathPoint / PathResult / SeedCandidate

```python
@dataclass
class PathPoint:
    point_id: str                             # 稳定 ID,禁止重编号(建议 11 核心)
    frame_index: int
    reaction_coordinates: dict[str, float]
    arc_length: float
    progress: float
    geometry: ArtifactRef
    topology_valid: bool
    energies_hartree: dict[str, float | None] # 多方法共存:xTB/B97-3c
    diagnostics: dict
    provenance: Provenance

@dataclass
class PathResult:
    strategy_id: str
    strategy_version: str
    points: list[PathPoint]
    complete: bool
    endpoint_evidence: dict
    topology_segments: list
    candidates: list[SeedCandidate]           # 统一结果 schema,非统一算法
    artifacts: dict

@dataclass
class SeedCandidate:                          # RPH=1TS+1INT / guided-scan=Top3+Top2 / GSM 自由
    id: str
    kind: Literal["ts_seed", "intermediate_seed"]
    geometry: ArtifactRef
    rank: int
    selection_mode: str                       # e.g. "endpoint_knee_shift_midpoint_v1" / "local_max_prominence"
    confidence: str
    evidence: dict
    stationary_point_claimed: bool = False    # seed ≠ 驻点(建议 12)
```

### 5.5 化学网络 = 有向多重图(非 DAG)

```python
@dataclass
class StableStateNode:
    state_id: str
    canonical_geometry: ArtifactRef
    ensemble: StableStateEnsemble | None
    charge: int
    multiplicity: int
    identity_fingerprint: str

@dataclass
class ElementaryStepEdge:
    step_id: str
    source_state_id: str
    sink_state_id: str
    ts_id: str
    path_strategy: str
    coordinate_plan: ReactionCoordinatePlan
    irc_connectivity: dict
    barrier_forward: float | None
    barrier_reverse: float | None
    fidelity: str
    status: str                               # discovered / confirmed / closed

# 化学网络允许:A ⇌ B、B ⇌ C、C → A;A → TS1 → B 与 A → TS2 → B 并存
# 执行图(Execution Graph)可以是 DAG,化学网络(Reaction Network)是有向多重图 —— 两者分离
```

### 5.6 FrontierQueue(非递归)与 DecisionPoint(持久化人工门)

```python
@dataclass
class ExplorationFrontier:
    queue: deque[tuple[str, str]]             # [(state_id, route_id), ...]
    max_depth: int = 5
    # 支持:resume / branch pruning / 并行 route / user pause

@dataclass
class DecisionPoint:
    id: str
    type: Literal["mechanism_frontier_review"]
    status: Literal["waiting", "resolved", "superseded"]
    options: list[str]                        # ["continue", "promote_to_s4", "stop_branch", "edit_route"]
    payload: dict                             # 该点证据(energy profile/IRC 连通/TS 虚频模式)
    created_at: str
    resolved_at: str | None
    resolution: str | None
```

### 5.7 统一 Quality Gate

```python
@dataclass
class QualityGateResult:
    gate_id: str                              # G0..G5
    status: Literal["pass", "warn", "fail"]
    evidence: dict
    thresholds: dict
    missing_evidence: list[str]
    suggested_action: str | None
```

---

## 6. Provider 契约层

```python
# acp/mechanism/providers/contracts.py
class EnsembleProvider(Protocol):                      # S1
    def generate(self, stable_state, profile) -> StableStateEnsemble: ...

class PathSearchStrategy(Protocol):                    # S2
    def search(self, source_state, target_state,
               coordinate_plan, profile) -> PathResult: ...

class RefinementProvider(Protocol):                    # S3/S4 共用
    def refine(self, requests: list[StationaryPointRequest],
               fidelity: FidelityProfile) -> RefinementManifest: ...

class EndpointProvider(Protocol):                      # SR
    def run_irc(self, ts, fidelity) -> IrcResult: ...
    def classify_endpoints(self, irc_result, known_states) -> EndpointMatchResult: ...

class ThermochemistryProvider(Protocol):               # 热力学
    def compute(self, sp_energy, freq_log, ensemble,
                temperature, standard_state) -> ThermochemistryResult: ...
```

**统一产物契约(建议 12/13):**

- `SeedCandidate[]` 是 S2→S3 唯一交接物(不再要求各策略同数量);
- `RefinementManifest` 是 S3/S4 唯一产物(canonical winner + attempts + manifest hash),模仿 RPH `refinement_manifest_v1`;
- `PathResult` 自带 `strategy_id/strategy_version` → 顶层 Orchestrator **不感知**内部有无 B97 SP(建议 12:SP 精化归策略内部 scientific policy)。

**S2 两条策略的明确定义(建议 10):**

- **Strategy A — guided-scan**(ACP 自己的通用方法,GUI 驱动):
  `StableState + ReactionCoordinatePlan → λ=0→1 per-frame constrained xTB opt → previous geometry seeds next → optional B97-3c SP → generic candidate selector(Top-N)`。支持 distance/angle/dihedral/multiple coupled coordinates。返回 TS Top-N + INT Top-N。
- **Strategy B — rph-reverse**(严格实现 RPH v4.0.1):
  `product representative → coarse reverse PEB(0.20 Å step / policy_c)→ topology guard → last-valid anchor → xTB2 metadynamics PATH(~28 frames)→ xTB SP → B97-3c SP on ALL PATH frames(SHA256 geometry cache)→ endpoint/knee/right-shift selector(1×TS + 1×INT)`。
- **direct-ts**:跳过扫描,直用 TS 初猜(1×TS)。

---

## 7. 阶段语义与 ACP 落点(顶层 6 阶段)

> **明确不做**(相对 v1.0 方案的修正):❌ 不再增加 `conformer_ensemble / scan_sp_reeval / int_optimize / irc_endpoint_extract / final_sp_reeval` 五个顶层 stage → SP 精化收进 PathStrategy(建议 12)、INT 优化收进 RefinementEngine(建议 14)、端点提取收进 EndpointProvider(建议 19);❌ 不再"给 mechanism.py 打 stage 补丁"。

| 顶层阶段 | 语义 | 内部 subtask(以 events 呈现) | ACP 落点 |
|---|---|---|---|
| **S0** Reaction Definition | 原子身份契约 + 坐标计划 + 电荷自旋 | atom mapping 编译、coordinate validation | 新 stage(替换 prepare_reaction);**G0 gate** |
| **S1** Stable-State Ensembles | 稳定点构象系综 | CREST/GFN2 → dedup → (B97-3c SP + xTB mRRHO)* 或 xtb-fast | 新 `EnsembleProvider`;**G1** |
| **S2** Path Discovery | PEB/扫描找初猜 | PEB → PATH → SP(策略内)→ SeedCandidate | 新 `PathSearchStrategy`(guided-scan / rph-reverse / direct-ts);**G2** |
| **S3** Low-Fidelity Refinement | RefinementEngine P0–P3(TS+INT 统一) | Preflight/Primary/Rescue/Canonical | 新 `RefinementProvider`(rph-s3 或 guided-scan 低保真);**G3** |
| **SR** Network Expansion | IRC → EndpointMatch → Frontier 扩展 | IRC/MEP → minimum+Freq → endpoint classify → new route | 新 EndpointProvider + FrontierQueue;**G4** |
| **S4** High-Fidelity Confirmation | 同一 RefinementEngine + FidelityProfile(s4) | canonical Freq → SP → thermo | `RefinementProvider`(rph-s4)+ promotion_policy;**G5** |

**S1 两种模式命名约定(建议 1,科学含义不混淆):**

| 模式 | 定义 | 适用 |
|---|---|---|
| `rph-censo-lite` | CREST/GFN2 + **B97-3c SP(每个 unique conformer)** + xTB mRRHO + G_i = E_B97-3c + G(RRHO)_xTB + partition function | RPH 语义严格 parity |
| `xtb-fast` | CREST/GFN2 + xTB ranking/dedup(无 DFT SP) | ACP 自有快速模式 |

> 注意:rph-censo-lite **不是** "纯 xTB" —— 它有 B97-3c SP。`xtb-fast` 才是纯 xTB。v1.0 方案中"censo-lite = 纯 xTB 推荐路线"的表述已修正。

---

## 8. Quality Gate 体系(G0–G5)

统一 `QualityGateResult`,写入 study 级 `quality_gates.json`,前端可直接渲染(建议 27)。

| Gate | 检查内容 | 关键判据 |
|---|---|---|
| **G0** Reaction Definition | atom identity mapping、坐标原子引用、charge/multiplicity、coordinate validity | AtomIdentityMap 完整;CoordinateSpec.atom_refs 全部可编译到几何索引 |
| **G1** Stable-State Ensemble | 至少 1 有效 conformer、系综完整性、能量排序、热力学完整性、representative 几何 | 各稳定点 ≥1 conformer;ranking 可用;rph-censo-lite 下 B97 SP 完整性(strategy 定义) |
| **G2** Path Quality | path complete、energy coverage、topology corridor、endpoint evidence、seed evidence | 由 **strategy 定义 policy**(RPH:profile complete+B97 full coverage+valid corridor+effective endpoint+knee+TS seed;guided-scan:path 完整+Top-N seed) |
| **G3** Stationary Point | TS:Opt 收敛+Hessian index=1+虚频曲率+**mode_match**+identity;INT/minimum:0 显著虚频+未意外塌缩+identity evidence | `mode_match_score` 通用 RC alignment;INT 用 evidence 聚合 |
| **G4** Elementary Step / Network | validated TS、IRC 完整、双端点解析、端点态分类、连通性建立 | IRC 两方向均到端点;EndpointMatcher 无 AMBIGUOUS 未处理 |
| **G5** High-Fidelity Confirmation | S4 几何有效、Hessian identity 保持、SP 可用、thermo 可用、S3↔S4 定性一致 | 势垒趋势一致;缺任一 → fail + suggested_action |

---

## 9. S3/S4:RefinementEngine + FidelityProfile

> 核心原则(建议 4/14/24):**不实现 `stage_s3_xxx` / `stage_s4_xxx` 两套驻点代码。** 这是 RPH 当前最成熟、最值得迁移的架构 —— 已核实 RPH v4.0.1 的 `RefinementEngine` 是单类实现、`FidelityProfile` 是 immutable `@dataclass(frozen=True)`,engine 内**无任何 S3/S4 科学分支**(stage 来自注入的 profile)。

```python
# ACP 侧 RefinementProvider 的 RPH 实现(adapter 直连 rph_core.steps.refinement.engine.RefinementEngine)
low  = RefinementProvider(rph_engine, FidelityProfile("rph-s3"))   # B97-3c → r2SCAN-3c
high = RefinementProvider(rph_engine, FidelityProfile("rph-s4"))   # M062X → wB97M-V
```

### 9.1 真实 Fidelity 对照表(已按 RPH v4.0.1 核实)

| | S3 | S4 |
|---|---|---|
| Geometry | B97-3c | M062X/def2-SVP |
| Grid/SCF | default | DefGrid3/TightSCF |
| Freq | B97-3c | M062X/def2-SVP |
| SP | r2SCAN-3c | wB97M-V/def2-TZVPP |
| Solvent | CPCM acetone | CPCM acetone |
| INT warmup | 8 cycles | 4 |
| TS warmup | 12 cycles | 6 |
| Initial Hessian precursor/product | model | model |
| Initial Hessian INT/TS | calculate | calculate |

### 9.2 四 Pass 语义(照搬 RPH `_run_pass0_preflight/1/2/3`)

- **P0 — Preflight**:XYZ 合法、charge/multiplicity、atom index、coordinate/reaction plan、work dir、provenance、AttemptRecorder。ACP 对应 `StationaryPointRequest` 校验 + `AtomIdentityMap` 编译。
- **P1 — Primary**:role-based constrained warmup(INT/TS warmup,precursor/product 无)+ 独立 Opt/OptTS + **独立 Freq**。RPH 明确禁止用 Opt Hessian 替代 independent Freq —— ACP 直接继承该原则。
- **P2 — Rescue**:**8-cell 有序矩阵**(核实版,非笛卡尔 28 cell):

| | R1 Restart | R2 Mode | R3 Exact | R4 IRC |
|---|---|---|---|---|
| **F1** geometry not converged | TS→fresh_hessian_restart→ts_mode_directed→calcall_opt;INT/MIN→… | | | |
| **F2** higher-order saddle | TS→saddle_break→ts_mode_directed→calcall_opt | | | |
| **F3** TS no imaginary | TS→fresh_hessian_mode_monitor→ts_mode_directed→calcall_opt | | | |
| **F4** min with imaginary | INT→mode_displacement;MIN→… | | | |
| **F5** SCF / **F6** crash-timeout | **无 cell —— 故意不自动救援**(照搬 RPH 语义) | | | |
| **F7** INT collapsed to product | INT→irc_midpoint_recovery | | | |

  cell 内**顺序执行,首个产出 valid candidate 即终止**;ACP 救援关键词需真正落地到 ORCA 输入生成(现状 `ts_mode/mode_displacement/opt_level/irc_midpoint_reseed` 被 orca.py:1210 静默丢弃 → 见 §14 能力缺口 1)。
- **P3 — Canonical**:primary + rescue candidates 全部进 `StationaryPointCandidatePool`(建议 6,不删失败历史)→ canonical sort(stationary_rank→mode_rank→Hessian index→converged→mode alignment→gradient norm)→ `canonical.xyz` → **Canonical Freq → SP → thermochemistry**(建议 24:SP 必须自然执行,`sp_kwargs` 不被消费 = contract broken,而不是顶层补 stage)。

### 9.3 S4 promotion_policy(建议 25)

`"all_confirmed"`(小体系默认)/ `"rate_relevant"`(低能+低势垒+竞争 TS)/ `"user_selected"`(GUI 勾选)三档,数据模型预留字段。

---

## 10. TS/INT Identity(泛化)

### 10.1 通用 `mode_match_score`(建议 15,替代现 identity.py 的 ±0.05 Å 固定位移)

- 对虚频 mode **v**,沿 ±δ(**normalized mode amplitude**,非固定 Å,因 angle/dihedral 单位不同)位移 `x± = xTS ± δ·v`;
- 对每个 drive coordinate 测 `Δqi = qi(x+) − qi(x−)`,按用户预期方向(distance decreasing→−,increasing→+,dihedral→+)加权匹配:

```
S = Σ wi·|Δqi|·I(direction matched) / Σ wi·|Δqi|     →  0..1
```

- 使 forming/breaking bond、proton transfer、torsional、multi-coordinate cycloaddition 共用同一 scorer。

### 10.2 INT identity(建议 16,泛化 `classify_int_v2`)

不再用 `topology_sane=True` 硬编码;聚合 `StableStateIdentityEvidence`:

- stationary order(驻点阶数)
- connectivity signature
- reaction-coordinate state
- RMSD to known stable states(mapped)
- energy relationship(TS/INT/product 能量排序)
- charge / multiplicity
- missing evidence

IRCFrame 端点分类复用 RPH `utils/irc_trajectory.py` 的 `parse_irc_trajectories / classify_irc_endpoints / detect_intermediate_shoulder`(已核实存在,直接 adapter)。

---

## 11. SR:反应网络 + FrontierQueue + EndpointMatcher

### 11.1 完整链路(建议 19/20/21)

```
IRC raw endpoint
        ↓
unconstrained minimum optimization        ← IRC endpoint ≠ StableState(必须先最小化)
        ↓
Freq
        ↓
Stable-state validation
        ↓
EndpointMatcher ──► {MATCH_EXISTING | NEW_STATE | AMBIGUOUS | FAILED}
        ↓
NEW_STATE
        ↓
StableState registration
        ↓
S1 conformer normalization                ← 选 representative ensemble members
        ↓
enqueue(frontier) ──► 下一轮 S2
```

- **IRC endpoint ≠ StableState**(建议 19 核心):端点先做无约束最小化+频率,再注册,再 S1 规范化 —— 避免后续路径依赖偶然构象。
- **EndpointMatcher(建议 20)**:evidence = atom mapping + connectivity fingerprint + charge/multiplicity + **mapped heavy-atom RMSD(端点 vs 已知态构象系综取最小)** + reaction-coordinate signature + energy neighborhood;返回四态分类。
- **化学网络 = directed multigraph**(建议 17);执行图(DAG)与化学网络分离。
- **FrontierQueue 而非递归**(建议 18):

```python
while frontier:
    exploration = frontier.pop()          # (state_id, route_id)
    path = search_path(exploration)
    refined = refine(path.candidates)
    edges = resolve_irc(refined)
    update_network(edges)
    enqueue_new_routes()
```

  天然支持 resume / branch pruning / max depth / 并行 route / user pause。
- **IRC 两语义严格分离**(建议 22):`irc_for_connectivity`(network discovery)vs `irc_midpoint_rescue`(rescue policy 内部),不共用同一函数语义。IRC 只在 valid_target_ts 后运行,不作为泛化 INT 救援。

---

## 12. Checkpoint / Provenance / Resume

### 12.1 Study 级目录布局(建议 31)

```
mechanism_study/<study_id>/
├── study.json                  # MechanismStudy 序列化(含 atom_identity_map + gates 摘要)
├── network.json                # ReactionNetwork(有向多重图,MechanismRoute.to_dict 现成)
├── states/state_XXX/           # 每稳定点:ensemble_manifest.json + state fingerprint
├── routes/route_XXX/           # path_manifest.json(PathResult + strategy 版本)
├── refinements/ref_XXX/        # refinement_manifest_v1(attempts + canonical + sha256)
├── decisions/decision_XXX.json # DecisionPoint(持久化人工门)
└── events.jsonl                # study 级事件流
```

### 12.2 复用现有基座(全部已核实存在)

| 需求 | 现有基座 | 落点 |
|---|---|---|
| stage 状态存储 | `WorkflowState` + `state.json` 原子写 | state.py:59,136-156 |
| JSONL 事件流 | `EventLog` / `JobEventLog` | state.py:30-46 / events.py:23-76 |
| SHA-256 指纹 | `compute_input_hash` / `compute_checksum` | provenance.py:78 / artifacts.py:133 |
| hash-validated resume | `_stage_fingerprint`/`_file_sha256`/`_resume_or_rerun` | xtbmd_censo_energy.py:883-962(**泛化到 state/route/task 粒度**) |
| 新表迁移 | migrations.py 框架(**006+**) | migrations.py:22-93 |
| per-stage task 状态机 | `StageTask` + `StageTaskObserver`(`retry_count` 现成) | stage_tasks.py:25-40,322-446 |
| study 容器(现成未用) | `MechanismStudy` | mechanism/models.py:192(**首次实例化**) |

### 12.3 Resume 粒度与 Provenance(建议 30/31)

- Resume 粒度 = **state fingerprint / route fingerprint / task fingerprint / artifact hash**(非 `iteration 2`);
- 每结果记录 provenance:

```json
{
  "provider": "rph",
  "provider_version": "4.0.1",
  "provider_commit": "3abbaecdd0b3c8cad6c4106c6e3ea07b6071e437",
  "strategy": "rph-reverse",
  "strategy_version": "...",
  "profile_id": "b97_3c_r2scan_3c_v1",
  "schema_version": "...",
  "input_signature": "sha256:..."
}
```

- 远端同步复用 scheduler `remote/sync.py` 的 mtime 同步把 RPH 仓库推上节点。

---

## 13. CLI / Catalog / Scheduler / API / 前端 注册面

### 13.1 CLI(cli.py mechanism parser :893-988 扩展)

- `acp run mechanism --study-id <id> --conformer-mode {auto,censo-lite,xtb-fast} --max-elementary-steps N --int-extension --promotion-policy {all_confirmed,rate_relevant,user_selected} --auto-converge`;
- `acp mechanism resume --study <id>`(新 run 子命令,dispatch 表 cli.py:2091 注册;建议 23:CLI resume 只是 API 的另一种入口);
- 修复奇偶断裂:`--scan-points/--irc-points` 补入 parser(jobs.py:288 已发射,CLI 未收 → 必须补)。

### 13.2 Catalog / 调度器(jobs.py:285-327 是 E7 唯一发射源)

- `_MECHANISM_SCALAR_FLAGS` 追加 `conformer_mode/max_elementary_steps/int_extension/promotion_policy` 同名 snake_case key(local runner:750 与 remote script_gen:176 自动奇偶一致);
- `METHOD_SCHEMAS["mechanism"]`(catalog.py:1854)method_levels 保持 5 级不动,profiles 增加新 entry(`censo-lite` 走 `censo_ensemble` schema:1337,`guided-scan-fast` 走 mechanism 新增 profile);
- `FIDELITY_PROFILES`(presets.py:89)与 catalog profiles 保持双向同步(防 defaults 分叉反模式)。

### 13.3 API / 前端(现状盘点:无 mechanism-study 路由、无 WAITING 状态、无网络可视化)

- 新增 `/api/v1/mechanism-studies` CRUD + `GET /api/v1/mechanism-studies/{id}/report` + **`POST /api/v1/mechanism-studies/{id}/decisions/{id}`**(建议 23);Pydantic 模型进 v1_schemas.py,表进 migrations `006+`;
- `JobStatus`(jobs.py:29-51)新增 `WAITING_REVIEW` 值并加入 `is_active`;`manager._poll_loop`(manager.py:952)排除该状态、`_requeue_active_on_startup`(:986)不得标 FAILED、`cancel/delete/move` 继承现有 active-block 语义;新增 `manager.pause_for_review/resume`;
- 前端 v2:mechanism builder 面板(:1393-1440)增加 review 按钮 + `renderInfoInto`(:3244)增加 DecisionPoint 卡片;**反应路径/能量图 tabs(:1159-1160,现为空壳)渲染 reaction network 图**,数据源 = `GET /report`(PathResult 能量 + 网络节点/边)。

### 13.4 热力学 Provider(建议 26)

- 现状:ThermoCalculator protocol(base.py:162-182)存在但**零工作流消费**;`dG_std/qRRHO` 全缺(grep 无 standard-state 概念);`batch_process_thermo` 有单 sp_energy footgun(runners:314-318)。
- 新 `ThermochemistryProvider` 契约(base.py:162 旁新增),实现:
  - `ShermoProvider`(默认,封装 run_shermo,修复 batch footgun);
  - `RPHCompositeProvider`:`G_composite = E_SP + (G_freq − E_freq) + ΔG_ensemble + ΔG°_std`(Shermo 的 `-E` 已隐含前三项,runners:216);
  - 未来 GoodVibes。
- **ΔG°_std / qRRHO 为净新增**,进 provider 层共享;
- mechanism.py:749 energy_analysis 与 energy_shared.py:412 run_rank1_handoff 改走 provider,不再直接 `import run_shermo`。

---

## 14. 能力缺口清单(必须新增的底层代码)

| # | 缺口 | 现状证据 | 方案级描述 |
|---|---|---|---|
| 1 | **ORCA 输入生成扩展** | orca_ts.py:124 ts_geom_block;orca.py:1210 仅消费 solvent/solvent_model/grid/scf/nproc | 支持 `ts_mode`(`%geom TS_Mode`)、`mode_displacement`(虚频位移叠加)、`opt_level`、`irc_midpoint_reseed` —— 否则救援矩阵 R2/R3/R4 族策略仍为死代码 |
| 2 | **IRC 端点几何提取** | parse_irc_endpoints(orca_ts.py:211)只回路径;IrcResult.final_geometries 从不填充 | 扩展为解析坐标填充 final_geometries;IRC `InitHess Read` 需从 ts_opt stage 复制 `.hess` |
| 3 | **`irc` capability 注册** | registry._CAPABILITY_PROTOCOLS + capabilities.py 无 irc alias | 补注册,使 `require_backend("irc")` 合法 |
| 4 | **ORCA 版本检测** | software.py:77 `_VERSION_FLAGS["orca"] = ()` | 补 `--version` 探测,"ORCA 6.1.1" 门控关键词 |
| 5 | **WorkflowRunner 无 resume/skip/gate** | workflow.py:86 严格线性 | Study Orchestrator 内部实现 FrontierQueue 驱动,不改造通用 runner(保持平台其他工作流不受影响) |
| 6 | **DecisionPoint 持久化 + WAITING_REVIEW 状态机** | 全新增 | §13.3 |
| 7 | **ΔG°_std / qRRHO 热力学修正** | 全库无 standard-state 概念 | 净新增,进 Thermo Provider |
| 8 | **mode_vector 填充** | TsOptResult.mode_vector(orca_ts.py:51)从不填充 | TS 频率解析时提取虚频位移向量,供 mode_match_score 使用 |

---

## 15. 里程碑 M0–M4

| 里程碑 | 内容 | 验证门 | 与现状 ACP 关系 |
|---|---|---|---|
| **M0 Contract-first** | 数据模型(§5)+ Provider 契约(§6)+ QualityGate 框架 + fake providers | fake provider 端到端构建两步反应网络(含 DecisionPoint/resume) | 不动 QC;纯新增 `acp/mechanism/` 包收敛;`MechanismStudy` 首次实例化 |
| **M1 RPH Parity** | RPH Adapter(直连 `CensoLiteEngine/RefinementEngine/FidelityProfile/V4Checkpoint/select_path_seeds`)+ `rph-s3/s4` 保真 | **ACP 与 standalone RPH 同输入同 profile 结果 scientifically equivalent**(镜像 RPH 测试契约:test_refinement_pass0-3 / test_rescue_matrix / test_v4_checkpoint / test_s2_unified_selection) | 复用现有 mechanism 9-stage 的 QC 执行;RPH 仓库经 PYTHONPATH/同步接入 |
| **M2 ACP Generalization** | `guided-scan` 成为第二套策略:GUI 3D 坐标选择(distance/angle/dihedral × drive/freeze/monitor)、multi-coordinate relaxed scan、Top-N candidate pool、通用 mode_match_score、xtb-fast ensemble | G0/G1/G2 全过;guided-scan 与 rph-reverse 结果可对照 | 新 Provider 契约承接;旧 stage 函数改写为内部 subtask |
| **M3 SR / Network** | IRC endpoint 提取、minimum validation、EndpointMatcher、StableState registry、FrontierQueue、cycle detection、route fingerprint、DecisionPoint + resume | G4 全过;两步基元反应自动完成两次 SR;端点四态分类正确 | 新 EndpointProvider + Orchestrator;调度器 WAITING_REVIEW 状态 |
| **M4 Publication-grade** | S4 promotion_policy、canonical 高保真精化、composite thermo(Shermo/RPH)、网络 profile、route comparison、Workbench 可视化 | G5 全过;产出 `reaction_network.json / mechanism_profile.json / stationary_points.json / quality_gates.json / provenance.json`;971 全量回归 + 新增机制专项测试 | ThermoProvider 接线;前端 network 图渲染 |

---

## 16. 验收标准(彻底可用化定义)

一条命令端到端跑通:

```bash
acp run mechanism --input R --product P --preset rph-s4 \
  --conformer-mode auto --max-elementary-steps 3
```

1. **端到端可用**:S0→S1→S2→S3→SR→S4 全链路,产出五件 JSON 报告(`reaction_network.json / mechanism_profile.json / stationary_points.json / quality_gates.json / provenance.json`)+ Workbench 可视化;
2. **递归正确性**:两步基元反应(如 A→INT→B)自动发现 INT 并完成两次 SR;
3. **救援有效性**:对人为构造的失败算例(不收敛/高阶鞍点/塌缩),救援链 ≥2 步生效且记录日志;
4. **门禁完整**:G0–G5 全部执行并写入 report;`mode_match_score` 与虚频判据联合生效;
5. **回归**:971 全量测试通过 + 新增机制专项测试;local/remote 奇偶一致;
6. **RPH Parity**:同输入同 profile 下 ACP 与 standalone RPH scientifically equivalent。

---

## 附录 A:RPH v4.0.1 主线核实记录

**仓库身份**(已 clone 到 v4.0.1 tag 逐文件核实):

| 项 | 值 |
|---|---|
| URL | `https://github.com/PengYangchao0808/ReactionProfileHunter` |
| Owner | `PengYangchao0808`(个人账号,非 GrimmeGroup;grimme-lab 组织下无此项目) |
| 默认分支 | `main` |
| v4.0.1 tag | tag `v4.0.1` → commit `3abbaecdd0b3c8cad6c4106c6e3ea07b6071e437` |
| Release | "v4.0.1 — Unified S3/S4 RefinementEngine",2026-08-12 |
| License | MIT(© 2026 Peng Yangchao) |
| 规模 | ~50k 行 / 198 文件 |

**关键文件核实**:

| 文件 | 内容 |
|---|---|
| `rph_core/steps/conformer_search/censo_lite.py` | CENSO-LITE 11 步链(RDKit ETKDGv3 → CREST GFN2/ALPB → split → xTB cross-validation → torsion+RMSD dedup → ORCA B97-3c SP → xTB mRRHO → G_i = E_B97-3c + G(RRHO)_xTB → partition function) |
| `rph_core/steps/step2_retro/path_selector.py` | `endpoint_knee_shift_midpoint_v1`:ts_right_shift_base_A=0.15 / span_fraction=0.10 / min=0.05 / max=0.40 / knee_min_curvature_signal=0.05 / stationary_point_claimed=False |
| `rph_core/steps/refinement/engine.py` | `RefinementEngine` 单类四 pass:`_run_pass0_preflight / _run_pass1_primary_one / _run_pass2_rescue / _run_pass3_canonical`,无 S3/S4 分支 |
| `rph_core/steps/fidelity_profile.py` | `@dataclass(frozen=True)`,profiles `b97_3c_r2scan_3c_v1`(S3)/ `m062x_wb97mv_v1`(S4) |
| `rph_core/steps/refinement/rescue_matrix.py` | **恰好 8 个有效 cell**:F1×{TS,INT,MIN}、F2×TS、F3×TS、F4×{INT,MIN}、F7×INT;F5/F6 无 cell 直接退出;F7 = "INT collapsed to product minimum" |
| `rph_core/utils/identity.py` | `classify_int_v2`(evidence-based)、`classify_ts` |
| `rph_core/utils/irc_trajectory.py` | `IRCFrame/IRCTrajectory/IRCShoulder`(frozen dataclass)+ `parse_irc_trajectories/classify_irc_endpoints/detect_intermediate_shoulder` |
| `rph_core/utils/v4_checkpoint.py` | `pipeline.state`,schema `rph_v4_checkpoint_v2`;SHA-256 签名(`signature`=sha256(sort_keys JSON)、`file_signature`=流式 sha256) |
| `rph_core/version.py` | `__version__ = "4.0.1"`(单源) |

**重要发现(影响 Adapter 设计)**:

1. **无 `rph_core/api/` 公共层、无 `RPHMethod` 类、不可 pip 安装**(无 pyproject)。建议 29 的"薄 API 层"是 RPH 侧**待补**状态。ACP Adapter 首版直接锚定具体类:`V4Orchestrator` / `CensoLiteEngine` / `RefinementEngine` / `FidelityProfile` / `V4Checkpoint` / `select_path_seeds`;`provider_version` 取 `rph_core.version.__version__`。
2. **`bin/rph` 在 v4.0.1 已损坏**(import 不存在的 `rph_core.orchestrator`);用 `bin/rph_run` 或 `python -m rph_core`。Adapter 永不依赖 bin 脚本。
3. 绝对导入风格(`rph_core...`)→ 可直接放 PYTHONPATH;ACP remote/sync.py 可同步。

---

## 附录 B:相对 v1.0 方案的修订对照表

| # | v1.0 方案 | v2.0 修订 | 依据 |
|---|---|---|---|
| 1 | S1 CENSO-lite = xTB-only(纯 xTB 推荐路线) | 拆成 `rph-censo-lite`(含 B97-3c SP + xTB mRRHO)与 `xtb-fast`(纯 xTB) | 建议 1;RPH censo_lite.py 核实 |
| 2 | S2 通用 bond-stretch ≈ RPH | guided-scan 与 rph-reverse 严格分离;rph-reverse 定义为具体 RPH 语义(anchor/PATH/full-B97 profile) | 建议 2/10 |
| 3 | RPH Top-3 TS/Top-2 INT | 更正:那是 ACP selector;RPH 当前是一 TS + 一 INT(knee+right-shift selector);统一 `SeedCandidate[]` | 建议 3 |
| 4 | bond_stretch 新 coordinate kind | 底层保持 `distance/angle/dihedral × drive/freeze/monitor`;Form/Break/Rotate 是 GUI 语义 | 建议 9 |
| 5 | scan_sp_reeval 顶层 stage | 收进 PathStrategy 内部 scientific policy;顶层不感知 B97 SP | 建议 12 |
| 6 | S3 分散 TS/INT/rescue stages | 统一 `RefinementEngine` P0–P3;TS/INT 一起处理 | 建议 14 |
| 7 | F1–F7 × R1–R4 笛卡尔矩阵 | 按 RPH 8-cell ordered rescue matrix;F5/F6 故意不救援 | 建议 5;rescue_matrix.py 核实 |
| 8 | IRC endpoint → 直接下一 S2 | 先 minimum/Freq → EndpointMatch → S1 normalization → enqueue | 建议 19 |
| 9 | ReactionNetwork DAG | 改为 directed multigraph;执行图 DAG 与化学网络分离 | 建议 17 |
| 10 | CLI input(y/n) 人工门 | 持久化 `WAITING_REVIEW` DecisionPoint + API + resume | 建议 23 |
| 11 | S4 独立 final_sp_reeval stage | 同一 RefinementEngine + FidelityProfile;P3 canonical 自然执行 SP | 建议 24 |
| 12 | Top-3 TS 全 refine 取成功 | 升级为 `StationaryPointCandidatePool` + canonical winner(不删失败历史) | 建议 6 |
| 13 | 新增 5 个顶层 stage(14-15 stage 化) | 顶层只留 6 阶段;内部任务全部 subtask/events | 建议 32 |
| 14 | mode_match ±0.05 Å 固定位移 | normalized mode amplitude δ + 通用 RC alignment | 建议 15 |
| 15 | topology_sane 硬编码 bool | evidence-based StableStateIdentityEvidence | 建议 16 |
| 16 | SR 递归函数 | FrontierQueue(while frontier 循环) | 建议 18 |
| 17 | Shermo → Gtotal 写死 | ThermochemistryProvider 契约(Shermo/RPH-composite/GoodVibes) | 建议 26 |
| 18 | G1–G5 散在 stage | 统一 QualityGateResult 六门 G0–G5 | 建议 27 |
| 19 | —(v1.0 未覆盖) | AtomIdentityMap(G0 gate)+ coordinate atom_refs uid 编译 | 建议 8 |
| 20 | —(v1.0 未覆盖) | RPH Adapter 依赖 rph_core/api/(不存在)→ 首版直连具体类,标记 RPH 侧待补薄 API 层 | 附录 A |

---

## 附录 C:内化完成记录(v3.0,2026-08-15)

**状态**:RPH 科学引擎已全部内化为 ACP 原生实现;`rph_adapter.py` 降级为 parity 对照工具(`config['mechanism']['provider_backend'] = 'rph'` 显式启用)。

### C.1 原生引擎与外部对应物

| 阶段 | 原生引擎 | 替代的 rph_core | 关键复用(CCCP/ACP) |
|---|---|---|---|
| S1 | `NativeCensoLiteProvider` | `CensoLiteEngine`(1621 行+4 依赖) | CrestBackend.search / XTBBackend.single_point+enso_thermo / batch_single_point / torsion_dedup / boltzmann |
| S2 | `NativeReversePebStrategy` | `PEBScanEngine`(4341 行) | ORCAInterface.relaxed_scan / XTBPathInterface.path_search(xtb --path) / primitives 六模块 |
| S3/S4 | `NativeRefinementProvider` | `RefinementEngine`(4389 行) | ORCABackend 全方法 / constrained_optimize / rescue 8-cell / refinement_manifest_v1 |

### C.2 新增基元任务词汇(8 类)

上浮:`constrained_optimize`(XTB)、`enso_thermo`(mRRHO);新增:`xtb --path`(XTBPathInterface)、ORCA relaxed_scan、ORCA constrained_optimize、`batch_single_point`(并行+SHA256 缓存);纯算法:`torsion_dedup`、path_selector/path_profile 族(`acp/mechanism/primitives/`)。

### C.3 行为变化

- `conformer_mode=auto` 现默认 **censo-lite(native)**(原:RPH 可用时才 censo-lite,否则 xtb-fast);
- study 模式不再要求 ReactionProfileHunter checkout;`provider_backend='rph'` 仅用于同输入 parity 对照;
- rescue 矩阵达 RPH 8-cell 完整对齐(F1×INT/MIN 补齐,F3 顺序修正,MethodParams 粒度);
- FidelityProfile 新增 role-based warmup/Hessian/cycle 预算字段(s3 40/50/60,s4 4/6/200)。

### C.4 Parity 入口

同输入对照:`acp run mechanism ... ` 分别以默认(native)与 `--config`(含 `mechanism.provider_backend: rph`)运行,比较 `refinements/*/refinement_manifest.json` 与 `states/*/ensemble_manifest.json`。判定:canonical 几何 RMSD<0.05 Å、ΔE<1e-4 Ha、排序 Spearman>0.95。
