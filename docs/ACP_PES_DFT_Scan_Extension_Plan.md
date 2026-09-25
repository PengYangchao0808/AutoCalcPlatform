# PESsearch 可选计算级别约束几何优化扫描 — 前后端完整修改方案

> **文档状态**：第一阶段已实施（2026-09-21）。后端（levels.py / catalog / contracts / orca 接口 / scan.py / CLI）、前端（Workbench v2 六项改造 + job_editor 兼容回填）、验收测试（tests/test_acp_pes_dft_scan.py + remote_phase6/job_edit/frontend_sync 扩展）全部落地
> **生成日期**：2026-09-21
> **范围**：将 PESsearch 扩展为"可选择计算级别的约束几何优化扫描"，首批支持 B97-3c、r²SCAN-3c、B3LYP，保留现有 xTB 路径，单点能精修继续独立配置
> **验收标准**："参数确实进入计算" + "结果可追溯"

---

## 0. 设计目标与四层界面结构

目标链路（与 Workbench 创建向导的三层配置一致）：

```
扫描坐标与驱动 → 扫描点几何优化（可选计算级别） → 可选单点能精修（独立配置） → 候选结构筛选
```

关键区别：选择 DFT 扫描后，**每个扫描点的几何结构都在对应 DFT 势能面上优化**（逐点约束优化 / ORCA 原生 relaxed scan）；只把单点能改为 DFT 并不改变此前的 xTB 几何路径。

分阶段交付：

| 阶段 | 交付内容 | 本文档 |
|---|---|---|
| **第一阶段** | 完整 DFT 接入：三类目标方法、参数模型、目录与界面联动、两条执行路径参数贯通、创建/编辑/CLI/远程一致性 | ✅ 本方案 |
| 第二阶段 | 统一逐点执行、可靠重试与续算（checkpoint）、局部区间加密、双向扫描比较 | 预留挂钩，不设计细节 |

---

## 1. 现状链路核对结论（事实基础）

以下全部结论基于对当前代码的逐行核对，file:line 均已验证。

### 1.1 参数流全链路（已验证）

```
前端向导 (schema-driven: openMethodConfig → buildLevelCard → buildFieldRow)
  → buildPESProtocolFromMethod() 组装 protocol dict        [v2.html ~L26070]
  → spec.input.scan_request.protocol                        [提交]
  → 调度器: runner._build_pessearch_cmd (runner.py:1448)     bond-scan 模式写 scan_config.json → --scan-config
  → CLI: _build_bond_scan_request (cli.py:1250)              protocol = dict(scan_config["protocol"]) 全量透传 ✓ (L1308)
  → contracts.ScanProtocol.from_dict → ScanOptimizer.from_dict (contracts.py:239-257)
  → pes/scan.py::_run_relaxed_scan_backend (scan.py:487-502) ← 唯一后端调用点
  → backends/orca.py::relaxed_scan (thin pass-through, **kwargs)
  → cccp ORCAInterface.relaxed_scan (orca.py:1890) → _build_input_blocks (orca.py:1202) 关键字发射
```

**关键结论**：调度器 bond-scan 路径经 `scan_config.json` 全量透传 protocol dict（cli.py:1308 已核实），新增 scan_optimizer 子字段**无需改动 runner/script_gen 即可流达 CLI**——前提是 contracts 层解析它们。

### 1.2 缺口清单（核对确认，含两处现状 bug）

| # | 缺口 | 位置 | 性质 |
|---|------|------|------|
| G1 | `scan_optimizer_method` 枚举仅 3 个 GFN 方法；scan_optimizer level `allowed_engines: ["xtb"]` | catalog.py:1202-1208, 2859 | 目录限制 |
| G2 | `ScanOptimizer` 无 basis/dispersion/solvent/grid/SCF 字段 | contracts.py:229-257 | 契约缺口 |
| G3 | **`scan_optimizer.convergence` 从未传给后端**（ORCA `_OPT_LEVEL_MAP`、xtb `opt_level` 均已支持但未接线） | scan.py:487-502 | "可设置未采用" |
| G4 | **`scan_optimizer.retry_count / retry_strategy`、`scan_driver.failure_policy / retry_count / reuse_previous_geometry` 从未生效** | scan.py:487-502 | 同上 |
| G5 | **`SinglePointSpec.solvent` 未转发给 BatchSinglePointExecutor**——溶剂模型传了、溶剂名丢了 | scan.py:834-857 | 现状 bug |
| G6 | ORCA 接口 `grid`、`dispersion` 非命名参数（只能 route_extras 散传）；单点能链的 `grid` 疑似同样未真正进入输入 | orca.py:1202-1482 | 接口缺口 |
| G7 | ORCA 逐点路径 `_run_synchronous_relaxed_scan` fail_fast 硬编码 True、`constrained_optimize` 失败不调用 `classify_orca_failure` | orca.py:2047-2177 | 重试基础缺失 |
| G8 | 旧任务 `method.levels.scan_optimizer.engine == "xtb"`，若 allowed_engines 改 `["orca"]` 后旧任务编辑重算会被 `normalize_and_validate_method_config` 拒绝（L4508） | catalog.py:4298 迁移钩子未覆盖 | 兼容风险 |
| G9 | 每帧记录缺：实际计算级别、SCF 状态、重试历史、level 指纹 | contracts.py ScanFrame / pes_profile.json | 可追溯性缺口 |
| G10 | 前端 scan_optimizer 区仅 5 个字段、方法无分组、无 ORCA 可用性检查、无"复制计算级别"、无提交摘要 | v2.html（schema 驱动自动跟随目录） | 前端缺口 |

### 1.3 有利事实（可直接复用）

- `METHOD_META`（catalog.py:452-579）已含 `basis_inline / builtin_dispersion / ri_support / default_basis / default_dispersion`，`r2SCAN-3c`、`B97-3c` 元数据齐备；
- ORCA `_build_input_blocks` 对复合方法已正确处理：`basis_inline=False` → route 不带基组；`ri_support="composite"` → 剥离 RI/辅助基组；`builtin_dispersion` 存在时从 extras 剥离色散关键字（orca.py:1350-1363）——**3c 方法"不重复基组/色散"的底层保证已存在**；
- 前端 3c 方法 basis/dispersion 锁定联动逻辑已存在（v2.html ~24312），只需把 `scan_optimizer_method` 注册为联动源；
- 编辑重算链无字段白名单（v1_routes.py:3701 `method` 全量透传、Pydantic `extra="allow"`），`compute_source_revision` 哈希整个 method dict——新字段自动参与 revision/diff；
- single_point level 已是完整 DFT 计算级别模型，字段定义全局共享——扫描优化器按同构模式扩展即可；
- `classify_orca_failure`（orca.py:57-112）已有 SCF/几何/内存/崩溃四分类，可直接用于逐点重试决策。

---

## 2. 总体架构：共享计算级别模型

新增模块 **`src/acp/calculations/levels.py`**（唯一规范化点，前端/单点能/扫描不再各自维护规则）：

```python
@dataclass(frozen=True)
class CalculationLevel:
    method: str
    basis: str | None = None
    dispersion: str | None = None
    solvent_model: str = "none"      # "none" 显式表示不用溶剂（空值语义见 §5.2）
    solvent: str | None = None
    grid: str | None = None
    scf_convergence: str | None = None
    scf_max_iterations: int | None = None
    ri_approximation: str = "none"
    aux_j_basis: str | None = None
    aux_c_basis: str | None = None

# 模块级 API
normalize_method_alias(raw: str) -> str          # "b973c"→"B97-3c", "r2scan-3c"/"R2scan3c"→"r2SCAN-3c",
                                                # "gfn2"/"GFN2"→"GFN2-xTB", "b3lyp"→"B3LYP"
canonical_level(level: CalculationLevel) -> CalculationLevel
        # 查 METHOD_META：3c 方法锁定 basis/dispersion=None（内置）、剥 RI/aux；
        # B3LYP 等 basis_inline 方法应用 default_basis/default_dispersion
engine_for_method(method: str) -> str            # GFN* 与 DFT 一律 "orca"（PES 链路扫描始终经 ORCA 子进程）
validate_level_for_purpose(level, purpose) -> list[str]
        # purpose="scan_optimization"：要求 METHOD_META.capabilities.scan_optimization
level_fingerprint(level) -> str                  # canonical 后 sha256[:16]，供 manifest/checkpoint/SP cache
```

**能力目录**：`METHOD_META` 每个方法条目增加 `"capabilities": {"gradient": bool, "optimization": bool, "scan_optimization": bool}`。首批标 `scan_optimization: true`：

- GFN2-xTB / GFN1-xTB / GFN-FF
- B97-3c / r²SCAN-3c（复合方法，锁定配套基组与修正，不允许叠加另一套色散或替换基组）
- B3LYP（保留不加色散的选项以便复现已有方案）
- PBE0（后续通过能力目录扩展）

高价方法（DLPNO-CCSD(T)、双杂化等）不标——**能力筛选保证扫描点优化列表 ≠ 单点能列表的全量复制**。

依赖方向：`levels.py` → `acp.catalog`（单向，catalog 不反向依赖，无环）。

---

## 3. 后端修改计划

### 3.1 方法目录与 schema（`src/acp/catalog.py`）

#### (a) FIELD_DEFINITIONS 扩展

scan_optimizer level 字段族，全部带 `scan_optimizer_` 前缀，与现有 5 字段同构：

| 新字段 | 类型 | options / 默认 | 说明 |
|---|---|---|---|
| `scan_optimizer_method`（扩展） | select 分组 | `["GFN2-xTB","GFN1-xTB","GFN-FF","B97-3c","r2SCAN-3c","B3LYP","PBE0"]` + `option_groups: [{xtb},{composite_dft},{conventional_dft}]` 元数据 | 默认仍 `GFN2-xTB`；大小写规范化走 `normalize_method_alias` |
| `scan_optimizer_basis` | select, supports_custom | 依赖所选方法动态解析（B3LYP→def2-SVP 默认；3c→显示"内置"锁定） | |
| `scan_optimizer_dispersion` | select | none/D3/D3BJ/D4；3c 方法锁定"内置" | |
| `scan_optimizer_solvent_model` | select | none/CPCM/SMD（默认 none） | |
| `scan_optimizer_solvent` | select | depends_on solvent_model | |
| `scan_optimizer_grid` | select, advanced | **DefGrid1/DefGrid2/DefGrid3**（ORCA 原生命名，label 注明精度档；默认留空=ORCA 缺省） | 不复用 single_point 的 SG1/Fine 命名，避免二次映射 |
| `scan_optimizer_scf_convergence` | select, advanced | normal/tight/verytight | |
| `scan_optimizer_scf_max_iterations` | int, advanced | 默认 200 | |
| `scan_optimizer_ri_approximation` | select, advanced | none/RI/RIJCOSX/RIJK；3c 锁定 | |

#### (b) METHOD_SCHEMAS["pes_scan"] 修改

- `scan_optimizer` level：`allowed_engines: ["xtb"] → ["orca"]`（L2859）。理由：PES 链路实际执行始终是 ORCA 子进程（native scan 或 constrained opt），GFN 方法以 ORCA 方法关键字形式写入——这同时修正 `derive_required_software` 把 xtb 二进制列为必需的偏差（多坐标路径现状走 ORCA `_run_synchronous_relaxed_scan`，非 XTBInterface）；
- fields 列表追加 (a) 全部新字段；
- `_resolve_field_options` / `_resolve_field_default`（L4094/L4149）增加 level 方法源映射：`_LEVEL_METHOD_FIELD = {"scan_optimizer": "scan_optimizer_method"}`，使 basis/dispersion/RI 选项按同 level 方法查 METHOD_META 联动（复用 functional 的现有联动机制）。

#### (c) profiles 扩展

保留 default 原值，旧任务 `profile_id:"default"` 回显零变化：

| profile_id | 扫描点优化 | 单点能 | 定位 |
|---|---|---|---|
| `default`（不变） | GFN2-xTB | B97-3c 开 | 现有默认行为（快速探索） |
| `economy-dft` | B97-3c（锁定内置） | 关 | 经济型 DFT，低成本 DFT 扫描入口 |
| `standard-dft` | r²SCAN-3c（锁定内置） | 关 | 新建 DFT 扫描的推荐默认预设 |
| `hybrid-dft` | B3LYP + D3BJ + def2-SVP | 关（可开 B3LYP/def2-TZVP） | 常规杂化 DFT，与研究计算级别一致 |

预设仅是 level 默认值集合**起点**，不锁定编辑，不给跨体系精度排名；DFT 预设单点能默认关闭以避免重复计算；旧预设（default）保持原有设置。

#### (d) `normalize_legacy_method`（L4298，store 读时迁移钩子）新增分支

**本方案最关键的兼容动作**：

```python
# 旧 PES 任务：levels.scan_optimizer.engine "xtb" → "orca"（读时一次性迁移），
# 否则 allowed_engines 改后旧任务"编辑重算"会在 normalize_and_validate L4508 被拒
# 新字段不注入（缺失即默认，from_dict 兜底），保证 spec 语义哈希除 engine 外不变
```

### 3.2 契约扩展（`src/acp/calculations/pes/contracts.py`）

`ScanOptimizer`（L229-257）增加字段并同步 `from_dict/to_dict`（缺省值保证旧 payload 解析行为不变）：

```python
class ScanOptimizer:
    method: str = "GFN2-xTB"
    basis: str | None = None
    dispersion: str | None = None
    solvent_model: str = "none"
    solvent: str | None = None
    grid: str | None = None
    scf_convergence: str | None = None
    scf_max_iterations: int | None = None
    ri_approximation: str = "none"
    aux_j_basis: str | None = None
    aux_c_basis: str | None = None
    # 现有控制字段保留：max_iterations / convergence / retry_count / retry_strategy
```

- `validate_scan_protocol`（L678-713）增加：
  - 方法 ∈ 目录 scan_optimization 能力集；
  - 3c 方法时 basis/dispersion/RI 必须为空（防绕过目录注入）；
  - `solvent_model != "none"` 时 solvent 必填（空值边界，见 §5.2）；
- `build_default_protocol`（L614）不动（默认仍是 GFN2-xTB）；
- **`ScanFrame` 增字段**：`optimizer_level: dict`（canonical CalculationLevel.to_dict()）、`optimizer_engine: str`、`scf_converged: bool | None`、`retry_history: tuple[dict, ...]`；
- `ScanProtocol` 增 `execution_mode: str`（`"native_scan" | "pointwise"`，由执行器回填，进 pes_profile.json）。

### 3.3 执行链参数贯通（`src/acp/calculations/pes/scan.py`）——第一阶段核心

重写 `_run_relaxed_scan_backend`（L453-505）后端调用段：

```python
level = canonical_level(level_from_spec(protocol.scan_optimizer))   # 共享模型规范化
result = backend.relaxed_scan(
    coords, symbols, output_dir=scan_dir, plan=plan,
    charge=charge, multiplicity=multiplicity,
    method=level.method, basis=level.basis, solvent=level.solvent,
    solvent_model=level.solvent_model,
    nprocs=nproc,
    use_scants=..., full_scan=...,
    geom_maxiter=int(protocol.scan_optimizer.max_iterations or protocol.scan_driver.max_iterations),
    opt_level=_OPT_LEVEL_ALIAS[protocol.scan_optimizer.convergence],   # G3 修复："very_tight"→"verytight"
    grid=level.grid, scf_convergence=level.scf_convergence,
    scf_maxiter=level.scf_max_iterations,
    ri_approximation=level.ri_approximation, aux_j_basis=..., aux_c_basis=...,
    dispersion=level.dispersion,                                     # G6：经新命名参数，不再 route_extras
    retry_count=protocol.scan_optimizer.retry_count,                 # G4：逐点路径生效（见 3.4c）
    retry_strategy=protocol.scan_optimizer.retry_strategy,
    failure_policy=protocol.scan_driver.failure_policy,
    point_callback=point_callback,
)
```

- **G5 修复**：`_run_single_points`（L834-857）构造 `BatchSinglePointExecutor` 时补 `solvent=sp_spec.solvent`；
- SP cache key 核对项：实现时确认 `BatchSinglePointExecutor`（cache_profile="pes_scan"）的 key 构成包含 SP 方法与帧几何，并混入 `level_fingerprint(scan_level)`——保证改方法后绝不复用旧能量；
- 候选门控收紧：`_recommend_candidates` 输入侧过滤 `optimization_converged && constraint_residual_ok` 的帧（现状仅全局 constraints_satisfied 抑制）——**只有收敛且约束达标的结构默认进入候选筛选**。

### 3.4 ORCA 接口统一（`src/cccp/qc/interfaces/orca.py` + orca_ts.py）

#### (a) `_build_input_blocks`（L1202-1482）增加命名参数

消除 route_extras 散传，单点能链同步受益：

- `grid`：`_GRID_KEYWORD_MAP = {"defgrid1": "DefGrid1", "defgrid2": "DefGrid2", "defgrid3": "DefGrid3"}` → route 关键字；
- `dispersion`：`{"d3": "D3", "d3bj": "D3BJ", "d4": "D4", "vv10": "VV10"}`，`"none"` 省略；3c 方法经现有 `builtin_dispersion` 剥离逻辑（L1361-1363）自动免疫重复；
- 两者进入 `relaxed_scan / _run_synchronous_relaxed_scan / constrained_optimize / single_point / optimize` 的 kwargs 面。

#### (b) 单坐标与多坐标路径参数对齐

- native 路径（L1890-2045）：`_orca_scan_route_settings`（L823-844）扩展接收完整 level（DFT 方法 + basis_inline 处理 + SMD %cpcm + grid/dispersion/SCF），不再只处理 GFN 溶剂特例；
- 逐点路径 `_run_synchronous_relaxed_scan`（L2047-2177）：`constrained_optimize` 调用透传同一 level 参数集；**解除 fail_fast 硬编码**——改为接收 `failure_policy/retry_count/retry_strategy`，失败帧走 (c)；
- `constrained_optimize`（L1805-1888）失败时调用 `classify_orca_failure`（L57-112），错误消息带分类标签（与 `optimize` 对齐）。

#### (c) 逐点重试语义（第一阶段仅逐点路径）

| retry_strategy | 行为 |
|---|---|
| `previous_geometry` | 用上一**收敛**帧几何重做种子 |
| `original_geometry` | 回到该点约束初始几何 |
| `looser_convergence` | opt_level 降一档（tight→normal）重试 |

- 重试**只允许改变初猜与数值收敛策略，绝不静默更换泛函/基组/溶剂**（canonical level 在循环外固定，循环内不可变）；
- 每次尝试记录 `retry_history` 条目（attempt / strategy / failure_class）；
- 超限后按 `failure_policy`：`mark_failed_continue` 继续后续点（帧标记未收敛、不进候选），`abort` 整体失败；
- native 单坐标路径：无逐点重试能力（单 subprocess）——`retry_*` kwargs 收到时 log warning 并忽略，`execution_mode="native_scan"` 让 UI 如实展示。**两种策略不得显示相同的恢复能力。**

#### (d) 资源

`%pal nprocs` / `%maxcore`（L1408-1409）已始终发射，`nproc`/`mem` 经 `spec.resources → --nproc/--mem → config → interface`（两条执行路径同一 config 对象）——第一阶段补验证测试而非改代码（见 §6 V-6）。

### 3.5 调度 / 编辑 / CLI / 远程一致性

- **runner / script_gen（bond-scan）**：无代码改动（scan_config.json 全量透传已核实，cli.py:1308）。**补 parity 回归测试**：`_build_pessearch_cmd` 与远程 bond-scan 命令尾部生成一致（E7 模式，参照 xtbmd `_XTBMD_SCALAR_FLAGS` 的 runner⇄script_gen 同源测试先例）；实现时按 E7 模式评估是否抽 `jobs.py` 共享 helper；
- **CLI（cli.py PESsearch 子命令）新增 flags**（`_build_bond_scan_request` L1348-1358 处 setdefault 进 protocol.scan_optimizer，不覆盖 scan-config 值）：
  - `--scan-basis / --scan-dispersion / --scan-solvent-model / --scan-solvent / --scan-grid / --scan-scf-convergence / --scan-scf-max-iter / --scan-ri-approximation`；
  - `--scan-method` 保持自由文本（下游 `normalize_method_alias` 规范化）；
- **编辑重算**：`EDIT_ACTIVE_WORKFLOWS` 已含 PESsearch、无白名单、revision/diff 自动覆盖新字段——**零代码改动**，补 API 级测试（现有 parametrized round-trip 的 PESsearch fixture 扩展 `levels.scan_optimizer` 含新字段）；
- **validate-method 端点**（v1_routes.py:4965）经 FIELD_DEFINITIONS 自动校验新字段，无改动；
- **旧任务三重保障**：① `normalize_legacy_method` engine 迁移（§3.1d）；② `ScanOptimizer.from_dict` 新字段缺省；③ 前端 hydrate 缺失字段用 schema 默认值填充显示（§4.6）。

### 3.6 结果与可追溯（pes_profile.json / result_manifest）

- `pes_profile.json` 顶层增加：`optimization_level`（canonical dict）、`optimization_level_fingerprint`、`execution_mode`（native_scan/pointwise）；
- 每帧（ScanFrame 序列化）携带 §3.2 新字段：优化收敛、SCF 状态、约束残差、几何、能量、实际计算级别、重试历史；
- 优化能（scan_energy）与单点能（single_point_energy）本就是分离字段、分离 series；SP 失败帧 `single_point_status="failed"` 保持不回填优化能——**单点失败时不得用优化能填补同一条曲线**（验收 V-10）；
- 扫描峰值仍只作 TS **候选**，确认链路不变（BatchOptimize → 频率/IRC），界面不直接标记"已确认过渡态"；
- 多坐标同步扫描继续表示一条路径；二维网格扫描为另一项功能，明确排除在本方案外。

---

## 4. 前端修改计划（`frontend/ACP_Workbench_v2.html` + `js/job_editor.js`）

表单主体由 schema 驱动（`openMethodConfig → buildLevelCard → buildFieldRow`），目录扩展后新字段**自动渲染**；前端改动集中在以下六点：

### 4.1 扫描点优化配置区（与单点能同构、独立保存）

- **方法分组下拉**：`buildFieldRow` 渲染 select 时消费 FIELD_DEFINITIONS 新增的 `option_groups` 元数据，输出 `<optgroup>`（xTB / 复合 DFT / 常规 DFT）；
- **3c 锁定联动**：把 `scan_optimizer_method` 注册进现有 functional→basis/dispersion 联动源列表（~L24312 逻辑复用）——选中 B97-3c / r²SCAN-3c 时 basis/dispersion/RI 显示"内置（锁定）"并禁用输入；B3LYP 时可编辑，且界面明确展示实际组合文本（如 `B3LYP-D3(BJ)/def2-SVP`）；
- **溶剂显式化**：`solvent_model=none` 显示"不使用溶剂"（显式 none，非空继承，见 §5.2）；
- **优化控制 / 高级 / 恢复**三组按 FIELD_DEFINITIONS 的 advanced 标记折叠分层（复用 single_point 的分层渲染）：
  - 优化控制：最大几何迭代数、几何收敛标准；
  - 高级：SCF 收敛、SCF 最大迭代数、积分网格、RI 与辅助基组；
  - 恢复：失败处理策略、重试次数、断点续算（断点续算第二阶段交付，第一阶段置灰并标注）。

### 4.2 引擎标识与可用性

- scan_optimizer level 引擎徽标改为 `orca`（catalog 改动自动生效）——选择 r²SCAN-3c 后显示 orca；
- 方法选择变更时查 `backendsCache`（`loadBackends()` 已维护）：orca 不可用 → 配置卡顶部警示条"当前节点未检测到 ORCA，DFT 扫描将无法执行"（不阻断编辑，提交摘要页给出红字）——**检查实际执行节点上的 ORCA 可用性**。

### 4.3 单点能区"复制扫描计算级别"

- single_point level 卡头部新增按钮：读取规范化后的 scan level（method/basis/dispersion/solvent_model/solvent/grid/scf_convergence）→ 写入 `wizardState.method.stages.single_point` → `rebuildLevelCard("single_point")`；
- 复制后完全独立可改（无持续绑定）。

### 4.4 预设选择器

- `#mc-profile-select` 已支持多 profile——pes_scan 4 个 profile 自动出现；
- `pickDefaultProfile` 的 PES 偏好保持 `default`（旧预设原行为，符合反模式 #27 的目录驱动原则）；
- DFT 预设 levels 中 `single_point.enabled=false` → 前端勾选框自动跟随。

### 4.5 提交摘要

`submitPESsearchTask` 提交前插入确认层（复用 s2scan summary-grid 样式模式）：

```
几何扫描：r2SCAN-3c / SMD(水) · 收敛 tight · 250 步 · 21 点
单点能：B3LYP-D3(BJ)/def2-TZVP / SMD(水)      （或：已关闭）
恢复策略：逐点重试 ×2（previous_geometry）      （native 单坐标时：整跑重试，无逐点重试）
```

- **恢复能力差异化**：坐标数 = 1（native）时禁用逐点重试字段并显示说明；多坐标时启用——两种策略不显示相同恢复能力。

### 4.6 编辑回显（job_editor.js）

- `pesSearchAdapter` 的 `hydrateMethodBase → baseMethodProjection → baseBuildMethod` 对 stages 通用，新字段自动往返；
- 补一处：旧任务 levels.scan_optimizer 无新字段时按 schema 默认值填充 `wizardState`（仅显示层，提交时以用户实际改动为准）。

---

## 5. 参数边界与规范化规则（全链路统一执行）

| # | 规则 | 落地点 |
|---|---|---|
| 1 | **电荷/多重度单一来源**：任务结构（`source.charge/multiplicity`）；扫描与单点能一律继承任务级值，`ScanOptimizer` 不引入 charge/multiplicity 字段；SP 调用已用任务级值（现状正确，保持） | scan.py 调用点 + validate 校验 SP 显式值与任务级一致否则告警 |
| 2 | **空值显式化**：内部表示 `solvent_model == "none"` 为显式无溶剂；`None`/缺失 → 解析为 `"none"`，**绝不回落全局溶剂配置**；`solvent_model != "none"` 且 solvent 缺失 → 校验错误 | `CalculationLevel` + `validate_scan_protocol` |
| 3 | **方法别名统一**：`normalize_method_alias` 在 contracts 解析、catalog 校验（canonical casing）、CLI 自由文本三处入口统一调用 | levels.py |
| 4 | **能力筛选**：扫描点优化方法列表 = `capabilities.scan_optimization` 过滤，禁止把单点能目录全量复制 | 目录 + validate |
| 5 | **资源实际生效**：nproc/mem 经 `spec.resources` 独立通道（本地 `--nproc/--mem` → config；远程 `#BSUB -n/-M` + 同 flags）→ interface `%pal/%maxcore`；两条扫描路径同一 config 实例；验收测试锁定 | V-6 |

---

## 6. 第一阶段验收清单（gate）

| # | 验收项 | 验证方式 | 测试落点 |
|---|---|---|---|
| V-1 | B97-3c / r²SCAN-3c 输入**无重复基组或色散**、无 RI/aux 关键字 | unit：断言 `_build_input_blocks` route 行 `! r2SCAN-3c Opt`、不含 basis token、不含 D3/D4、无 auxJ/auxC | tests/test_acp_pes_dft_scan.py（新） |
| V-2 | B3LYP 基组/色散/溶剂/SCF/网格/迭代全部正确进入 ORCA 输入 | unit：断言 `! B3LYP def2-SVP Opt D3BJ`、`%cpcm smd true + SMDsolvent "Water"`、`TightSCF`、`DefGrid2`、`%geom MaxIter`、`%pal/%maxcore` | 同上 |
| V-3 | **参数确实进入计算**：convergence→opt_level、retry、solvent、grid、dispersion kwargs 到达 `relaxed_scan`（monkeypatch 后端断言收参）；SP `solvent` 转发修复 | unit + 调用点断言 | 同上 + test_acp_pes_scan* |
| V-4 | 单坐标 native + 同步双坐标 pointwise 均输出有效帧（含 execution_mode 标注） | fake-backend 集成 | 同上 |
| V-5 | **旧 xTB 任务读取/回显/重跑不变**：golden spec fixture；`normalize_legacy_method` engine 迁移后编辑重算不 422；旧 profile default 回显一致 | 回归（关键兼容 gate） | test_acp_job_edit.py（fixture 扩展）+ test_acp_catalog* |
| V-6 | CLI / 本地 runner / 远程 script_gen 三方 bond-scan 命令行一致（含新 flags 不丢、资源 flags 进入两条执行路径） | 命令行快照对比（E7 parity 模式） | test_remote_* / runner 测试 |
| V-7 | 编辑重算完整保留新增参数（API 级 preview → submit → 回读） | API 集成 | test_acp_job_edit.py |
| V-8 | 真实 ORCA 三方法短扫描（各 ≤5 点小体系）出有效帧与能量 | `@slow` 标记，`--run-slow` 运行 | tests/test_acp_pes_dft_scan.py |
| V-9 | 前端契约：方法分组渲染、3c 锁定、复制按钮存在、提交摘要、恢复能力差异化（native 禁用逐点重试）、profile 目录驱动（动态读 catalog，防未来目录漂移） | 静态契约测试 | tests/test_frontend_sync.py（扩展，沿用 #27 的动态目录测试模式） |
| V-10 | 能量查看器扫描优化能与单点能**分离成两条曲线**，SP 失败帧不以优化能填补 SP 曲线 | 前端 + manifest 投影 | test_frontend_sync + results 投影测试 |

---

## 7. 实施顺序与依赖

```
第 1 步  levels.py + METHOD_META capabilities        （无依赖，纯新增）
第 2 步  catalog：字段/枚举/engine/profiles/联动/legacy 迁移   （依赖 1）
第 3 步  contracts：ScanOptimizer 扩展 + 校验 + ScanFrame 字段 （依赖 2）
第 4 步  cccp orca.py：grid/dispersion 命名参数 + 路径统一 + 失败分类 + 逐点重试（依赖 1，可与 2/3 并行）
第 5 步  pes/scan.py 调用点重写 + SP solvent 修复 + 候选门控  （依赖 3、4）
第 6 步  CLI flags + validate 联动 + parity 测试            （依赖 5）
第 7 步  前端六项改造 + 契约测试                            （依赖 2，可与 4/5 并行）
第 8 步  验收 gate V-1..V-10 + 真机 ORCA 短扫描             （收口）
```

改动面估算：后端 6 个文件（catalog / contracts / scan / orca / cli / levels 新增）+ 前端 1 个主文件 + job_editor.js；不触碰 runner/script_gen 逻辑（仅补测试）、不触碰编辑注册表。

---

## 8. 明确划入第二阶段（本轮不做）

- 统一逐点执行器（native 路径收敛入 pointwise）；
- 扫描中断点续算（checkpoint 含输入结构/坐标序列/level 指纹的完整机制——第一阶段先落 fingerprint 进 manifest 与 SP cache key）；
- 局部区间加密；
- 双向扫描比较；
- 二维网格扫描（独立设计）。

本方案已为它们预留挂钩：`execution_mode` 字段、`level_fingerprint`、逐点重试框架。

---

## 9. 残余风险提示

1. **`allowed_engines` 改 `["orca"]` 与旧任务 `engine:"xtb"` 的兼容**是全方案唯一的破坏性风险点，靠 `normalize_legacy_method` 读时迁移 + V-5 回归双保险；
2. **single_point 链的 `grid` 疑似同样存在"可设置未采用"**（G6 波及），第一阶段随 `_build_input_blocks` 命名参数化一并修复并补测试；
3. **远程 bond-scan 尾部与本地 runner 的 parity** 依赖测试固化而非代码同源，实现时按 E7 模式评估是否抽 `jobs.py` 共享 helper；
4. B3LYP 默认 def2-SVP（可选 def2-TZVP）与 METHOD_META 现有 `default_basis: "def2-TZVPP"` 存在档位差异——实现时在 pes_scan 的 profile/field default 中显式覆盖，避免与 simple 工作流默认值隐性冲突。
