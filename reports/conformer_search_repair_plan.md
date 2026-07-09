# ACP 构象搜索模块 — 完整修复方案

**生成时间**：2026-06-18
**依据**：6 个并行 agent 的代码审计 + CENSO/Molclus 权威文献调研 + Oracle 架构咨询
**目标**：将当前"工程外壳"升级为"协议语义正确"的最终 ACP 构象搜索系统

---

## 0. 诊断结论：偏离等级确认

经过对引擎路由、CENSO 实现、Backend 清单、Molclus 文献的全面审计，确认用户分析准确。以下偏离全部经代码验证：

| 偏离 | 严重度 | 代码证据 |
|------|--------|----------|
| `ext` 仍是 CREST+DFT，非 Molclus/xTB-MD | **P0** | `spec_adapter.py:109` 将 `molclus_xtb_md` 映射到 CREST；`stage_crest_search` 无 backend 分支 |
| `benchmark` 是 DLPNO SP，非 meta-suite | **P0** | `engine.py:246` `is_benchmark_protocol` → 同一 `_run_ext_protocol` |
| 旧协议未降级，与 CENSO 平级并存 | **P0** | `is_censo_protocol()` 硬编码吞掉 `full/lite/zero` |
| `censo-zero/lite` 输出 ensemble 而非 rank1 | **P0** | Part3 硬编码 `select_by_boltzmann_cutoff`；`select_mode` 字段从未被检查 |
| Part1/Part2/Part3 能量是 stub（复制 xTB 或 0.0） | **P0** | `_seed_censo_energy_key` fallback 到 `0.0`，TODO 注释确认 |
| `reference-sp` 从 embed 开始而非现有 ensemble | **P0** | 阶段序列 `embed → Part3` |
| `allopt` 未禁止 funnel 删除 | **P1** | 无协议级断言 |
| `SearchBackend` 是装饰性标签 | **P1** | `stage_crest_search` 无条件调用 CREST |
| 无 `molclus_backend.py` / `isostat_backend.py` | **P2** | 文件不存在 |
| `ExternalBackend.conformer_search` = NOT_IMPL | **P2** | 能力矩阵确认 |

**核心判断**：当前版本是"带 CENSO 路由的旧 CREST-DFT 引擎外壳"，不是最终设计的协议系统。

---

## 1. P0：协议语义纠正（最高优先级）

### 1.1 旧协议降级 + 别名迁移

**Oracle 建议（采纳）**：两层兼容方案——保留裸名可运行 + 新增 `legacy-*` 稳定别名 + 发出警告。

#### 改造方案

| 当前裸名 | 当前行为 | 新增别名 | 警告消息 |
|----------|----------|----------|----------|
| `ext` | CREST+DFT (wB97X-D4) | `legacy-ext` | "`ext` 当前是 legacy CREST+DFT；最终 `ext` 将变为 Molclus/xTB-MD。用 `legacy-ext` 锁定旧行为。" |
| `full` | 实际走 `_run_censo_protocol`（已被 `is_censo_protocol` 吞掉） | `legacy-full` | "`full` 当前重定向到 censo-full 管道；最终语义将变。用 `legacy-full` 或 `censo-full` 显式指定。" |
| `lite` | 同上，重定向到 censo-lite | `legacy-lite` | 同模式 |
| `zero` | 同上，重定向到 censo-zero | `legacy-zero` | 同模式 |
| `benchmark` | DLPNO SP | `legacy-benchmark` | "`benchmark` 当前是 DLPNO SP；最终将变为 meta-suite。用 `reference-sp` 做高级 SP。" |

**关键原则**：**不立即**把 `zero → censo-zero` 做语义重映射。理由：静默改变科学行为比显式警告更危险（Oracle 原文）。

#### 实施步骤

1. `spec_adapter.py`：`LEGACY_ALIASES` 新增 `legacy-ext→ext`、`legacy-full→full`（指向旧 `_run_ext/full/lite/zero_protocol` 实现的 registry 条目）
2. `engine.py:240-255`：dispatch chain 重构为 **registry 驱动**，移除 6 个 `is_*_protocol` 硬编码检查器
3. CLI `--protocol ext` 调用时：`import warnings; warnings.warn(...)` 发出 `DeprecationWarning`
4. `--list-protocols` 分组显示：`[official]` / `[legacy aliases]` / `[debug]`

### 1.2 `ext` → Molclus/xTB-MD（剥离 CREST）

**目标**：`ext` 只做外部构象生成（Molclus + xTB-MD），不进入 DFT。下游精炼通过 `--recipe` 组合。

**Oracle 建议（采纳）**：预阶段适配器模式——将不同 search backend 归一化为统一 ensemble 格式，再喂入现有 engine。

#### 改造方案

```
新协议组合语法（CLI）：
  acp conformer input.xyz --search ext --recipe censo-lite
  acp conformer input.xyz --search ext --recipe censo-full
  acp conformer input.xyz --search ext --recipe allopt
  acp conformer input.xyz --search ext --recipe none   # 仅生成 ensemble
```

#### 实施步骤

1. **新建 `src/acp/backends/molclus_backend.py`**（详见 §3.1）
2. **修复 `spec_adapter.py:109-110`**：移除 `molclus_xtb_md → crest_two_stage` 的错误映射
3. **重构 `stage_crest_search` → `stage_search`**：按 `workflow_spec.search.backend` 分支：
   - `"crest"` → `CrestBackend.search()`
   - `"molclus_xtb_md"` → `MolclusBackend.search()`（新增）
   - `"external_xyz"` → 跳过（直接用输入 ensemble）
   - `"rdkit"` → RDKit embed（现有逻辑）
4. **`ext` 协议 spec 更新**：`CensoRecipe` 设为 `none`，`EnergyProfile.final_sp_method=None`，`ThermoProfile.backend="none"`

### 1.3 `benchmark` → Meta-Protocol Suite

**Oracle 建议（采纳）**：独立 `BenchmarkRunner` 编排层，**不**作为 `ProtocolSpec` 子类或嵌套 `WorkflowSpec`。

#### 架构设计

```
BenchmarkRunner（新增 src/acp/workflows/benchmark.py）
  ├── 输入：单个结构或 ensemble + 协议列表
  ├── 共享准备：归一化输入一次
  ├── 子运行：每个协议独立子目录
  │     ├── benchmark/censo-zero/
  │     ├── benchmark/censo-lite/
  │     ├── benchmark/censo-full/
  │     ├── benchmark/allopt/
  │     └── benchmark/reference-sp/   ← 可选参考
  ├── 结果归一化：统一 conformer ID、能量、排名
  ├── 指标计算：
  │     ├── global_min_id（各协议找到的全局最低是否一致）
  │     ├── deltaG_vs_reference（相对 reference-sp 的 ΔG）
  │     ├── rank_spearman（排名 Spearman 相关）
  │     ├── boltzmann_overlap（Boltzmann 权重重叠）
  │     ├── n_sp / n_opt / n_freq（计算量）
  │     ├── walltime
  │     └── failure_rate
  └── 输出：benchmark_summary.json + 人读表格
```

#### 关键约束

- **conformer ID 稳定性**：所有子运行必须使用同一 ensemble 的原始 ID（CENSO 官方保证 ID 保持，见 §2.2）
- **参考可选**：`deltaG_vs_reference` 需要 `reference-sp` 子运行或外部参考数据
- **子运行隔离**：一个协议的 funnel 删除不影响另一个

### 1.4 `reference-sp` → 强制现有 ensemble

**问题**：当前 `reference-sp` 阶段是 `embed → Part3`，从 SMILES 开始只对单一结构做高级 SP，不是 ensemble refinement。

#### 改造方案

```python
# CLI 入口校验
if protocol == "reference-sp":
    if input_is_smiles or not path_exists(input):
        raise SystemExit(
            "reference-sp requires an existing conformer ensemble.\n"
            "Use censo-full or allopt first, then:\n"
            "  acp conformer <previous_output>/final_ensemble.xyz --protocol reference-sp"
        )
```

**阶段序列改为**：`input_ensemble → high_level_SP → optional_weight_recompute → output`（移除 `embed` 阶段）

### 1.5 `censo-zero/lite` → rank1 选择强制

**Librarian 确认（RTCONF55-16K）**：CENSO-zero/light 选择**单个 rank1 构象**，而非 ensemble。

#### 当前 Bug

- `CensoRecipe.select_mode = "rank1"`（specs.py）
- 但 `run_part3` 硬编码 `select_by_boltzmann_cutoff`（censo_parts.py:166-174）
- `select_mode` 字段**从未在运行时被检查**（死代码）

#### 改造方案

在 `_run_censo_protocol` 返回前，按 `select_mode` 做最终裁剪：

```python
# engine.py _run_censo_protocol 末尾
if recipe.select_mode == "rank1":
    records = records.select_rank1(KEY_FINAL_G, stage="final_selection")
elif recipe.select_mode == "topN" and recipe.top_n:
    records = records.select_top_n(KEY_FINAL_G, recipe.top_n, stage="final_selection")
# boltzmann_ensemble: 保持 Part3 的 Boltzmann cutoff 结果
```

### 1.6 CENSO Part1/Part2/Part3 真实能量计算

**当前**：`_seed_censo_energy_key` 复制 xTB 能量或回退到 `0.0`。Part1/2/3 无实际 QC。

**Librarian 确认 CENSO 官方语义**：

| Part | 方法级别 | 应执行的计算 | 能量 key |
|------|----------|-------------|----------|
| Part0 | B97-D3(0)/def2-SV(P)+gcp SP | ✅ 已实现（xTB SP 替代，可接受） | `xtb_sp` |
| Part1 | r2SCAN-3c/COSMO-RS SP | ❌ 未实现——需真实低本 DFT SP | `low_cost_dft_sp` |
| Part2 | r2SCAN-3c/DCOSMO-RS 几何优化 + xTB SPH 频率 | ❌ 未实现——需 DFT opt + freq | `r2scan3c_sp` |
| Part3 | 高级杂化 DFT SP + Shermo 热化学 | ❌ 未实现——需 final SP + Shermo | `final_sp`, `final_gibbs` |

#### 自由能组合公式（CENSO 官方）

```
G_i = E_high_level_SP(i) + δG_solv(i)(T) + G_mRRHO(i)(T)
```

- `E_high_level_SP`：高级 SP 电子能量（Part3）
- `δG_solv`：溶剂化自由能（DFT COSMO-RS）
- `G_mRRHO`：热化学修正，**始终在 xTB SPH 级别**计算（Part1/Part2 的频率）

#### 改造方案

用真实 QC 调用替换 `_seed_censo_energy_key` 的 stub：

```python
# Part1: 低本 DFT SP（r2SCAN-3c）
for record in records:
    result = orca_backend.single_point(record.coordinates, record.symbols, method="r2scan-3c")
    record.energies[KEY_LOWCOST] = result.energy

# Part2: DFT 几何优化 + 频率（r2SCAN-3c/DCOSMO-RS）
for record in records:
    opt_result = orca_backend.optimize(record.coordinates, record.symbols, method="r2scan-3c")
    freq_result = orca_backend.frequency(opt_result.coordinates, ...)
    record.energies[KEY_R2SCAN] = opt_result.energy
    record.thermo = extract_thermo(freq_result)  # G_mRRHO 来源

# Part3: 高级 SP + Shermo 热化学
for record in records:
    sp_result = gaussian_backend.single_point(record.coordinates, record.symbols, method="wb97x-d4")
    thermo_result = shermo_backend.thermochemistry(record.freq_log, temperature=298.15)
    record.energies[KEY_FINAL_E] = sp_result.energy
    record.energies[KEY_FINAL_G] = sp_result.energy + thermo_result.gibbs_correction
```

---

## 2. P1：协议注册表重排

### 2.1 协议分组

```
Official protocols（最终设计）:
  ext                  ← Molclus/xTB-MD external search
  censo-zero           ← xTB rank1 → high-level SP
  censo-lite           ← low-cost DFT SP rank1 → high-level SP
  censo-full           ← Grimme Part0-Part3 + Boltzmann ensemble
  censo-full-safe      ← 更保守窗口
  allopt               ← 穷举 DFT 验证（无 funnel 删除）
  reference-sp         ← 高级 SP on existing ensemble
  benchmark            ← meta-protocol suite

Legacy aliases（兼容，带警告）:
  legacy-ext           ← 旧 CREST+DFT (wB97X-D4)
  legacy-full          ← 旧完整筛选漏斗
  legacy-lite          ← 旧快速筛选
  legacy-zero          ← 旧仅单点
  legacy-benchmark     ← 旧 DLPNO SP

Soft redirects（便捷别名）:
  zero  → legacy-zero（警告）或 censo-zero（用户显式确认后）
  lite  → legacy-lite（警告）或 censo-lite
  full  → legacy-full（警告）或 censo-full
```

### 2.2 `allopt` 硬性约束

```python
# 协议级断言
if protocol == "allopt":
    assert recipe.part1_window_kcal is None or recipe.part1_window_kcal >= 100, \
        "allopt 禁止 Part1 window 删除构象"
    assert recipe.part2_window_kcal is None or recipe.part2_window_kcal >= 100, \
        "allopt 禁止 Part2 window 删除构象"
    # 仅允许 basic safety window（如 50 kcal/mol 防止明显错误构象）
    # 输出：exhaustive_conformer_thermo.csv
```

### 2.3 统一协议名来源

**当前问题**：5 处独立硬编码协议名列表（`SUPPORTED_PROTOCOLS`、`LEGACY_ALIASES`、`_LEGACY_STAGE_PROTOCOLS`、CLI `choices`、`ALL_PROTOCOLS`）。

**改造**：所有列表从 `PROTOCOL_REGISTRY`（specs.py）程序化派生：

```python
# 所有其他列表从单一来源派生
OFFICIAL_PROTOCOLS = [k for k, v in PROTOCOL_REGISTRY.items() if not k.startswith("legacy-")]
LEGACY_ALIASES_DERIVED = {k: v.legacy_alias_target for k, v in PROTOCOL_REGISTRY.items() if v.is_legacy}
SUPPORTED_PROTOCOLS = set(PROTOCOL_REGISTRY.keys())
```

---

## 3. P2：Backend 完成与 SearchBackend 真实分发

### 3.1 新建 `MolclusBackend`

**Librarian 确认 Molclus 工作流**：二进制闭源，子进程包装，无 Python API。

#### 文件结构

```
src/acp/backends/molclus_backend.py
  class MolclusBackend(QCBackend, ConformerSearcher):
      def search(self, initial_xyz, charge, multiplicity, output_dir) -> Path:
          # 1. xTB-MD 轨迹生成：xtb molecule.xyz --input md.inp --omd --gfn 0
          # 2. GFN0 批量优化：molclus (iprog=4, itask=0, gfn0)
          # 3. isostat 聚类：isostat isomers.xyz -Edis 0.5 -Gdis 0.25 -T 298.15
          # 4. GFN2 批量优化：molclus (iprog=4, itask=0, gfn2)
          # 5. isostat 聚类：→ cluster.xyz（最终 ensemble）
          return cluster_xyz_path

src/acp/backends/isostat_backend.py（可选拆分）
  class IsostatBackend(QCBackend, ClusteringTool):
      def cluster(self, ensemble_xyz, ...) -> Path:
          # subprocess isostat with -Nout/-Eout/-Edis/-Gdis/-T/-nt
```

#### 关键实现细节（来自 Librarian Molclus 调研）

- **每个阶段是独立的 `molclus` 调用**，有单独的 `settings.ini`
- **尴尬并行**：拆分 `traj.xyz`，多目录并行，合并 `isomers.xyz`
- **`isostat` 线程化**：`-nt N` 标志
- **输出格式**：`cluster.xyz` 是标准多帧 XYZ，注释行含能量 + Boltzmann 百分比，ACP `StructureEnsemble` 可原生解析
- **子进程模式**：`subprocess.run(["./molclus"], cwd=workdir, ...)` + 文件系统通信

### 3.2 SearchBackend 真实分发

**Oracle 建议（采纳）**：预阶段适配器——归一化所有 backend 输出为统一 ensemble 格式。

```python
# src/acp/workflows/conformer.py 新 stage_search
def stage_search(ctx, data, **params):
    spec = _workflow_spec_from_context(ctx)
    backend = spec.search.backend

    if backend == "crest":
        ensemble_xyz = engine.run_crest(initial_xyz)
    elif backend == "molclus_xtb_md":
        molclus = MolclusBackend(config)
        ensemble_xyz = molclus.search(initial_xyz, charge, multiplicity, output_dir)
    elif backend == "external_xyz":
        ensemble_xyz = initial_xyz  # 输入即 ensemble，跳过搜索
    elif backend == "rdkit":
        ensemble_xyz = engine.run_embed_smiles(initial_xyz)  # 现有 RDKit 逻辑
    else:
        raise ValueError(f"Unknown search backend: {backend}")

    ctx.params[_ENSEMBLE_XYZ_KEY] = ensemble_xyz
    return data
```

### 3.3 能力矩阵更新

新增 `molclus` 行到 `CAPABILITY_MATRIX`：

| Backend | conformer_search | clustering |
|---------|-----------------|------------|
| molclus | AVAILABLE | AVAILABLE (via isostat) |
| isostat | NOT_IMPL | AVAILABLE |

---

## 4. P3：协议级测试断言

### 4.1 CENSO 语义断言（来自 Librarian RTCONF55-16K）

```python
# tests/test_protocol_semantics.py（新增）

def test_censo_zero_no_dft_opt():
    """CENSO-zero 不执行 DFT 几何优化（仅 xTB 优化几何 + 高级 SP）。"""
    spec = resolve_any_protocol("censo-zero")
    assert spec.recipe.run_part2 is False  # Part2 是 DFT opt
    assert spec.recipe.select_mode == "rank1"

def test_censo_zero_selects_xtb_rank1():
    """CENSO-zero 最终选择 xTB rank1 单构象，非 ensemble。"""
    # 运行 mock pipeline，验证输出只有 1 个构象

def test_censo_lite_runs_low_cost_sp():
    """CENSO-lite 执行低本 DFT SP（r2SCAN-3c）用于重新排序。"""
    spec = resolve_any_protocol("censo-lite")
    assert spec.recipe.run_part1 is True
    assert spec.recipe.select_mode == "rank1"

def test_censo_lite_selects_sp_rank1():
    """CENSO-lite 最终选择 DFT-SP rank1 单构象。"""

def test_censo_full_runs_part2_optimization():
    """CENSO-full 执行 Part2 DFT 几何优化。"""
    spec = resolve_any_protocol("censo-full")
    assert spec.recipe.run_part2 is True
    assert spec.recipe.select_mode == "boltzmann_ensemble"

def test_censo_full_outputs_boltzmann_ensemble():
    """CENSO-full 输出 Boltzmann ensemble（99% population sum），非单构象。"""

def test_reference_sp_requires_existing_ensemble():
    """reference-sp 拒绝 SMILES 输入，要求现有 ensemble。"""
    with pytest.raises(SystemExit, match="requires an existing conformer ensemble"):
        run_conformer_search(input="CCO", protocol="reference-sp")

def test_ext_uses_molclus_backend_not_crest():
    """ext 协议调用 MolclusBackend，不调用 CREST。"""
    spec = resolve_any_protocol("ext")
    assert spec.search.backend == "molclus_xtb_md"
    # mock MolclusBackend.search，验证被调用；mock CrestBackend.search，验证未被调用

def test_benchmark_runs_multiple_protocols():
    """benchmark 运行多个协议并输出比较指标。"""
    # 验证 BenchmarkRunner 调用 N 个子协议

def test_allopt_forbids_funnel_deletion():
    """allopt 不允许 Part1/Part2 window 删除构象。"""
    spec = resolve_any_protocol("allopt")
    assert spec.recipe.part1_window_kcal is None or spec.recipe.part1_window_kcal >= 100
```

### 4.2 CENSO 能量正确性断言

```python
def test_censo_free_energy_formula():
    """验证 G_i = E_high_SP + δG_solv + G_mRRHO(xTB)。"""
    # mock 真实 QC，注入已知能量，验证 final_gibbs = final_sp + thermo.gibbs_correction

def test_conformer_id_preserved_across_parts():
    """CENSO 保持输入 ensemble 的构象顺序（CONF35 = 第35个构象）。"""
    # CENSO 官方保证（Librarian 确认）
```

---

## 5. 实施路线图（分阶段交付）

### 阶段 A：语义纠正（无新 backend，1-2 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 移除 `is_censo_protocol` 对 full/lite/zero 的吞掉 | `protocols.py:138-144` | — |
| dispatch chain 改为 registry 驱动 | `engine.py:240-255` | ↑ |
| `select_mode` 运行时强制（rank1 vs ensemble） | `engine.py:_run_censo_protocol` 末尾 | — |
| `reference-sp` 强制现有 ensemble | CLI + `conformer.py` | — |
| `allopt` window 约束断言 | `specs.py` + `conformer.py` | — |
| 旧协议 `legacy-*` 别名 + DeprecationWarning | `spec_adapter.py` + CLI | — |

**验收**：§4.1 测试断言全部通过（mock 模式）

### 阶段 B：真实 CENSO 能量（2-3 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| Part1 真实 r2SCAN-3c SP（替换 `_seed` stub） | `engine.py` + 新 QC 调用 | 阶段 A |
| Part2 真实 r2SCAN-3c opt + freq | `engine.py` | ↑ |
| Part3 真实高级 SP + Shermo 热化学 | `engine.py` | ↑ |
| 自由能组合 `G = E_high + δG_solv + G_mRRHO` | `engine.py` | ↑ |
| `select_mode` 按 RTCONF55-16K 定义执行 | `engine.py` | 阶段 A |

**验收**：mock QC 注入已知能量 → `final_gibbs` 正确组合；`test_censo_free_energy_formula` 通过

### 阶段 C：Molclus Backend + ext 剥离（3-5 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 新建 `molclus_backend.py`（子进程包装） | `src/acp/backends/` | — |
| 新建 `isostat_backend.py`（聚类子进程） | `src/acp/backends/` | — |
| 修复 `spec_adapter.py:109` molclus→CREST bug | `spec_adapter.py` | — |
| `stage_crest_search` → `stage_search`（backend 分支） | `conformer.py` | ↑ |
| `ext` spec 更新（recipe=none, search=molclus） | `specs.py` | — |
| 能力矩阵新增 molclus/isostat 行 | `capabilities.py` | — |
| `--search` + `--recipe` CLI 组合语法 | `cli.py` | ↑ |

**验收**：`test_ext_uses_molclus_backend_not_crest` 通过；`ext` 不再调用 CREST

### 阶段 D：Benchmark Meta-Protocol（2-3 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 新建 `BenchmarkRunner` 编排层 | `src/acp/workflows/benchmark.py` | 阶段 A-C |
| 子协议并行/串行执行 + 结果隔离 | ↑ | — |
| 指标计算（spearman/boltzmann_overlap/walltime） | ↑ | — |
| `benchmark_summary.json` + 人读表格输出 | ↑ | — |
| `benchmark` CLI 入口（`--benchmark-level quick/standard/strict`） | `cli.py` | ↑ |

**验收**：`test_benchmark_runs_multiple_protocols` 通过

### 阶段 E：协议注册表统一 + 文档（1 天）

| 任务 | 文件 | 依赖 |
|------|------|------|
| 5 处硬编码列表 → 从 `PROTOCOL_REGISTRY` 派生 | 多文件 | 阶段 A-D |
| `--list-protocols` 分组显示 | CLI | ↑ |
| CLI 帮助文档 + 迁移指南 | `README.md` / `docs/` | — |

---

## 6. 架构决策记录（ADR）

### ADR-1：旧协议迁移策略

**决策**：两层兼容——裸名保留 + `legacy-*` 别名 + DeprecationWarning
**否决方案**：硬弃用（风险高，部署是手动的）、静默重映射 `zero→censo-zero`（静默改变科学行为）
**依据**：Oracle 咨询

### ADR-2：Meta-Protocol 架构

**决策**：独立 `BenchmarkRunner` 编排层
**否决方案**：ProtocolSpec 子类（benchmark 不是单一工作流）、嵌套 WorkflowSpec（过度复杂）
**依据**：Oracle 咨询

### ADR-3：SearchBackend 分发

**决策**：预阶段适配器——归一化为统一 ensemble 格式
**否决方案**：完整 Strategy 模式 + backend-specific runner（高风险，重复编排/状态/日志）
**升级触发**：当 backend 差异超出搜索生成（如优化/排名/状态/清理生命周期不同）时迁移到 Strategy
**依据**：Oracle 咨询

### ADR-4：CENSO 自由能组合

**决策**：`G_i = E_high_level_SP + δG_solv + G_mRRHO(xTB)`
**依据**：CENSO 官方文档（fbohle.gitbook.io/censo）+ GEOM 数据集 + RTCONF55-16K
**关键**：热化学修正**始终在 xTB SPH 级别**，即使电子能在高级 DFT 级别

### ADR-5：Conformer ID 保持

**决策**：所有 CENSO 阶段保持输入 ensemble 的构象 ID 顺序
**依据**：CENSO 官方文档明确声明（"CONF35 will correspond to the 35th conformer of the input ensemble"）

---

## 7. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| Molclus 闭源二进制，环境依赖 | 中 | 子进程包装 + 清晰错误消息；CI 用 mock |
| Part1/Part2 真实 DFT 计算慢 | 高 | 分阶段交付；mock 模式先行；真实二进制 `--run-slow` |
| 旧脚本因 DeprecationWarning 中断 | 低 | 警告非错误；`legacy-*` 别名锁定行为 |
| `benchmark` 子运行 OOM/超时 | 中 | 子运行独立超时 + 失败隔离（不影响其他子运行） |
| `select_mode` 改动破坏现有 censo-full 测试 | 中 | 仅 censo-zero/lite 改 rank1；censo-full 保持 ensemble |

---

## 8. 验收门

修复完成后，以下门必须全部通过：

1. **协议语义断言**（§4.1）：全部通过
2. **`ext` 不调用 CREST**：mock 验证 MolclusBackend 被调用
3. **`benchmark` 运行多协议**：至少 3 个子协议 + 指标输出
4. **`reference-sp` 拒绝 SMILES**：`SystemExit` with 清晰消息
5. **`censo-zero` 输出单构象**：`select_mode="rank1"` 强制
6. **`censo-full` 输出 ensemble**：Boltzmann cutoff 保持
7. **CENSO 自由能公式**：`final_gibbs = final_sp + thermo_correction`
8. **旧协议兼容**：`legacy-ext` 仍产出旧 CREST+DFT 结果
9. **`--list-protocols` 分组**：official / legacy / debug 三组
10. **现有 198 测试不回归**

---

## 附录 A：CENSO Part0-Part3 权威定义

来源：CENSO 官方文档（fbohle.gitbook.io/censo）+ RTCONF55-16K（Mészáros et al., JCTC 2024）

| Part | 方法级别 | 优化？ | 频率(G_mRRHO)？ | 选择阈值 | 输出 |
|------|----------|--------|----------------|----------|------|
| Part0 | B97-D3(0)/def2-SV(P)+gcp SP | 否 | 否 | ~4 kcal/mol（电子能） | 缩减 ensemble |
| Part1 | r2SCAN-3c/COSMO-RS SP | 否 | 是（xTB SPH） | 电子能 + 全自由能双阈值 | 全自由能排序 |
| Part2 | r2SCAN-3c/DCOSMO-RS opt | 是 | 是（xTB SPH on opt geom） | Boltzmann sum（90-99%） | DFT 优化 ensemble |
| Part3 | 高级杂化 DFT SP | 否 | 否（继承 Part2） | Boltzmann sum（99%） | 精炼 Boltzmann 权重 |

## 附录 B：Molclus 工作流参考

来源：卢天 Molclus 官网（keinsci.com）+ 瑞德西韦教程（bbs.keinsci.com/thread-16255）

```
xtb molecule.xyz --input md.inp --omd --gfn 0   # MD 轨迹生成
  → xtb.trj → traj.xyz
molclus (iprog=4, itask=0, gfn0)                 # GFN0 批量优化
  → isomers.xyz
isostat isomers.xyz -Edis 0.5 -Gdis 0.25 -T 298.15  # 聚类去重
  → cluster.xyz → traj.xyz（循环）
molclus (iprog=4, itask=0, gfn2+gbsa)            # GFN2 批量优化
  → isomers.xyz
isostat                                          # 聚类
  → cluster.xyz
molclus (iprog=1/3, itask=3, template_SP)        # DFT opt+freq+高级SP
  → isomers.xyz
isostat -T 298.15                                # 最终聚类 + Boltzmann
  → cluster.xyz（最终结果）
```
