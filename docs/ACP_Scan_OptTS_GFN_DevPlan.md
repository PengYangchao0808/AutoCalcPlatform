# ACP Scan / OptTS / GFN 开发计划

**版本:** v1
**日期:** 2026-08-12
**状态:** 草案
**作者:** ACP Team

====================================================================

## 1. 概述

### 1.1 目标

为 ACP 平台增加三类能力：

| 编号 | 能力 | 用户入口 | 说明 |
|------|------|----------|------|
| **A** | ORCA 中 GFN 类方法支持 | 所有 ORCA simple 工作流 | 在 ORCA(而非 xtb 二进制)内使用 `GFN2-xTB` / `GFN1-xTB` / `GFN0-xTB` / `GFN-FF`,作为 method 供 singlepoint/optimize/frequency/scan/optts 调用 |
| **B** | ORCA 弛豫表面扫描 | `acp run scan` | 沿键长 / 键角 / 二面角进行弛豫势能面扫描 |
| **C** | ORCA 过渡态优化 | `acp run optts` | `OptTS` 过渡态(一阶鞍点)优化 |

### 1.2 关键发现(项目现状)

1. **`scan` 文档领先于实现** — `src/acp/workflows/AGENTS.md:17` 与 `src/acp/AGENTS.md:54` 已将 `scan` 列入 simple 工作流,但 `src/acp/workflows/simple.py:30-37` 的 `_STAGE_NAMES` 实际无 scan 条目。本计划将补齐实现。
2. **ORCA 接口扩展点清晰** — `src/cccp/qc/interfaces/orca.py:440-446` 的 `calc_type_map` 是 calc_type → ORCA 关键字的唯一映射;新增 calc 类型只需扩展此映射 + 增一个接口方法。
3. **METHOD_META 已支持"无基组"方法** — `basis_inline: False` 的条目(如 `r2SCAN-3c`,`src/acp/catalog.py:269`)输出 `! {method} {route}`(不带 basis),GFN 方法可复用。
4. **新增 simple 工作流的完整触点为 6 处后端 + 1 处前端**(见 §3)。
5. **v2 前端完全数据驱动** — 工作流卡片与方法表单由 `/api/v1/workflows` + `/api/v1/methods` 的 catalog 自动渲染;GFN 需一处手工字段隐藏逻辑。

### 1.3 非目标(Non-Goals)

- 不实现 NEB-TS(微带弹性带 TS 搜索)— 留待 mechanism 工作流增强。
- 不实现扫描的多坐标网格(同时扫两变量)— 首版仅支持单坐标(可多行顺序扫描)。
- 不改动 v1 工作台(`ACP_Workbench.html`)— 它是轻量仪表盘,无方法配置向导。

====================================================================

## 2. ORCA 语法参考(ORCA 5.x)

### 2.1 GFN 方法

ORCA 5.0+ 内置 xTB 半经验方法,作为普通 method 使用,**无需基组**:

```
! GFN2-xTB Opt TightSCF
! GFN1-xTB Freq
! GFN0-xTB SP
! GFN-FF Opt
```

- 溶剂:GFN 方法使用 **`ALPB(<solvent>)` 路由关键字**(非 `%cpcm` 块):
  ```
  ! GFN2-xTB Opt ALPB(water)
  ```
- 不使用 RI / aux basis / 色散校正 / 积分网格。

### 2.2 弛豫表面扫描(Relaxed Surface Scan)

通过 `%geom Scan` 块 + `! Opt` 触发,每个网格点固定被扫坐标、弛豫其余坐标:

```
! r2SCAN-3c Opt
%geom
  Scan
    B 0 1 = 1.0, 2.0, 10      # 键长(Å):原子0-1, 起→止, 步数
    A 0 1 2 = 90, 180, 9       # 键角(°)
    D 0 1 2 3 = 0, 360, 36     # 二面角(°)
  end
  MaxIter 100                  # 每点最大优化步数(可选)
end
```

- 行标识:`B`=bond(2 原子)、`A`=angle(3 原子)、`D`=dihedral(4 原子)。
- 输出含 `RELAXED SURFACE SCAN` 段,末尾汇总各点能量。

### 2.3 过渡态优化(OptTS)

`! OptTS` = 特征向量跟随法优化到一阶鞍点:

```
! B3LYP def2-TZVP OptTS TightSCF
%geom
  CalcFC                       # 可选:起点计算力常数
  inhess read                  # 可选:读取预计算 .hess
end
```

- 常配合先做 Freq 生成 Hessian,再 `inhess read` 读 `.hess`。
- 收敛后应做 Freq 验证恰好一个虚频。

====================================================================

## 3. 架构分析与触点清单

### 3.1 分层架构(自底向上)

```
cccp/qc/interfaces/orca.py   ← 路由块构建 + 子进程执行(唯一 subprocess 层)
        ↑
acp/backends/orca.py         ← 薄适配器,转发到 cccp 接口
        ↑
acp/workflows/simple.py      ← run_scan / run_optts 工作流函数
        ↑
acp/cli.py                   ← argparse 子命令 + handler + dispatch
        ↑
acp/scheduler/               ← jobs(派生) / stage_tasks(plan) / runner(_build_cmd) / remote/script_gen
        ↑
acp/catalog.py               ← WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS + FIELD_DEFINITIONS
        ↑
acp/api/v1_routes.py         ← /api/v1/workflows + /api/v1/methods(序列化 catalog)
        ↑
frontend/ACP_Workbench_v2.html ← 数据驱动渲染
```

### 3.2 触点清单(必改文件)

| 层 | 文件 | Part A(GFN) | Part B(scan) | Part C(optts) |
|----|------|:---:|:---:|:---:|
| cccp 接口 | `src/cccp/qc/interfaces/orca.py` | ✅ ALPB 路径 | ✅ scan() 方法 | ✅ ts_optimize() 方法 |
| 后端适配 | `src/acp/backends/orca.py` | — | ✅ scan() 转发 | ✅ ts_optimize() 转发 |
| 工作流 | `src/acp/workflows/simple.py` | ✅ GFN kwargs | ✅ run_scan() | ✅ run_optts() |
| 工作流导出 | `src/acp/workflows/__init__.py` | — | ✅ | ✅ |
| 注册表 | `src/acp/workflows/registry.py` | — | ✅ | ✅ |
| 目录 | `src/acp/catalog.py` | ✅ METHOD_META + 字段 | ✅ catalog + schema | ✅ catalog + schema |
| CLI | `src/acp/cli.py` | — | ✅ parser+handler | ✅ parser+handler |
| 调度-jobs | `src/acp/scheduler/jobs.py` | — | ✅ fallback 列表 | ✅ fallback 列表 |
| 调度-stage | `src/acp/scheduler/stage_tasks.py` | — | ✅ plan provider | ✅ plan provider |
| 调度-runner | `src/acp/scheduler/runner.py` | — | ✅ whitelist+分支 | ✅ whitelist+分支 |
| 调度-remote | `src/acp/scheduler/remote/script_gen.py` | — | ✅ whitelist+分支 | ✅ whitelist+分支 |
| 前端 | `frontend/ACP_Workbench_v2.html` | ✅ GFN 字段隐藏 | —(自动) | —(自动) |
| 测试 | `tests/test_acp_workflows_simple.py` | ✅ | ✅ | ✅ |
| 文档 | 多个 AGENTS.md | ✅ | ✅ | ✅ |

====================================================================

## 4. Part A — GFN 方法支持(ORCA)

> **依赖关系**: Part A 是基础设施,独立可测,且使 scan/optts 也能用 GFN 快速预扫。**建议首先实施**。

### A1. `src/cccp/qc/interfaces/orca.py` — ALPB 溶剂路径

**位置**: `_build_input_blocks()` 方法,约 line 590-597 的溶剂块。

**现状**: 所有溶剂走 `%cpcm smd true SMDsolvent "..."` 或 `%cpcm SMDsolvent "..."` 块。

**改动**: 在溶剂处理前,检测 method 属于 GFN 家族时,改走路由关键字 `ALPB(<solvent>)`:

```python
# 在 _build_input_blocks 内,solvent 处理段之前:
meta = _resolve_method_meta(_method)
is_gfn = bool(meta and meta.get("family", "").startswith("gfn"))

if _solvent and _solvent_model.lower() != "none":
    if is_gfn:
        # GFN 方法:ALPB 作为路由关键字
        _filtered_extras.append(f"ALPB({orca_smd_solvent(_solvent)})")
    else:
        # DFT:沿用 %cpcm 块(现有逻辑)
        blocks.append("%cpcm")
        ...
```

**注意**: `orca_smd_solvent()` 已是现有的溶剂名归一化函数(`cccp/utils/solvent_map.py`),复用即可。GFN 的 ALPB 溶剂名与 xtb 的 GBSA 溶剂名不同,使用 ORCA 的 ALPB 溶剂表。

### A2. `src/acp/catalog.py` — METHOD_META + 字段定义

#### A2.1 新增 4 个 GFN 条目

**位置**: `METHOD_META` 字典,`DLPNO-CCSD(T)` 条目之后(line ~397)。

```python
# ── GFN semi-empirical methods (ORCA built-in xTB; no basis, no RI) ──
"GFN2-xTB": {
    "basis_inline": False,
    "ri_support": "composite",
    "basis": (),
    "dispersion": ("none",),
    "builtin_dispersion": None,
    "default_basis": "",
    "default_dispersion": "none",
    "family": "gfn",
},
"GFN1-xTB": {  # 同 GFN2-xTB
    "basis_inline": False, "ri_support": "composite", "basis": (),
    "dispersion": ("none",), "builtin_dispersion": None,
    "default_basis": "", "default_dispersion": "none", "family": "gfn",
},
"GFN0-xTB": {  # 同上
    "basis_inline": False, "ri_support": "composite", "basis": (),
    "dispersion": ("none",), "builtin_dispersion": None,
    "default_basis": "", "default_dispersion": "none", "family": "gfn",
},
"GFN-FF": {  # 力场, family 标记区分
    "basis_inline": False, "ri_support": "composite", "basis": (),
    "dispersion": ("none",), "builtin_dispersion": None,
    "default_basis": "", "default_dispersion": "none", "family": "gfnff",
},
```

> `family` 字段是单一事实源,前端(§E1)与后端(§A1)都读它判断 GFN 行为。
> `_derive_functional_options_map()` (line 401) 会自动从 METHOD_META 派生 `functional_options_map`,无需手改。GFN 条目的 `basis: ()` → 派生为空列表。

#### A2.2 functional 可选项追加 GFN

**位置**: `FIELD_DEFINITIONS["functional"].per_backend["orca"]`(line ~433-439)。

```python
"per_backend": {
    "orca": [
        "r2SCAN-3c", "PBEh-3c", "B97-3c",
        "B3LYP", "PBE0", "M062X", "mPW1PW91",
        "wB97X-D4", "wB97M-V",
        "PWPB95", "revDSD-PBEP86",
        "DLPNO-CCSD(T)",
        # ↓ 新增 GFN 方法
        "GFN2-xTB", "GFN1-xTB", "GFN0-xTB", "GFN-FF",
    ],
    ...
},
```

#### A2.3 solvent_model 追加 ALPB

**位置**: `FIELD_DEFINITIONS["solvent_model"].per_backend["orca"]`(line ~459-463)。

```python
"per_backend": {
    "orca": ["none", "CPCM", "SMD", "ALPB"],   # 追加 ALPB
    "xtb": ["none", "ALPB", "GBSA"],
},
```

### A3. `src/acp/workflows/simple.py` — GFN kwargs 过滤

**位置**: `_build_method_kwargs()`(line 180)。

**现状**: 该函数已过滤空值、把 ri_approximation/dispersion 转 route_extras。但对 GFN 方法,basis/aux_basis/ri_approximation 字段无意义且可能注入脏值。

**改动**: 在函数末尾 return 前,加 GFN 清洗:

```python
# GFN 方法清洗:去除所有基组/RI/aux 相关字段(无意义)
method_val = str(kwargs.get("method", "")).strip()
_gfn_methods = {"gfn2-xtb", "gfn1-xtb", "gfn0-xtb", "gfn-ff"}
if method_val.lower() in _gfn_methods:
    for dead in ("basis", "aux_basis", "aux_j_basis", "aux_c_basis",
                 "ri_approximation", "dispersion", "geom_maxiter"):
        kwargs.pop(dead, None)
return kwargs
```

> `route_extras` 中由 dispersion/ri 转换来的项已在前面处理;此处只清 kwargs 顶层字段。

### A4. Part A 测试

**文件**: `tests/test_acp_workflows_simple.py`(或新建 `tests/test_orca_gfn.py`)。

```python
def test_method_meta_gfn_entries():
    from acp.catalog import METHOD_META
    for gfn in ("GFN2-xTB", "GFN1-xTB", "GFN0-xTB", "GFN-FF"):
        m = METHOD_META[gfn]
        assert m["basis_inline"] is False
        assert m["basis"] == ()
        assert m["family"].startswith("gfn")

def test_functional_options_map_gfn_basis_empty():
    from acp.catalog import FUNCTIONAL_OPTIONS_MAP
    assert FUNCTIONAL_OPTIONS_MAP["GFN2-xTB"]["basis"] == []

def test_orca_input_blocks_gfn_no_basis():
    from cccp.qc.interfaces.orca import ORCAInterface
    iface = ORCAInterface(config={})
    blocks, _ = iface._build_input_blocks("sp", method="GFN2-xTB")
    assert "GFN2-xTB" in blocks
    # 不应出现基组行 / %basis 块
    assert "def2" not in blocks
    assert "%basis" not in blocks

def test_orca_input_blocks_gfn_alpb_solvent():
    from cccp.qc.interfaces.orca import ORCAInterface
    iface = ORCAInterface(config={})
    blocks, _ = iface._build_input_blocks(
        "opt", method="GFN2-xTB", solvent="water", solvent_model="alpb")
    assert "ALPB(" in blocks
    assert "%cpcm" not in blocks  # GFN 不走 cpcm 块

def test_build_method_kwargs_gfn_strips_basis():
    from acp.workflows.simple import _build_method_kwargs
    kw = _build_method_kwargs({"method": "GFN2-xTB", "basis": "def2-SVP",
                                "ri_approximation": "RIJCOSX"})
    assert "basis" not in kw
    assert "ri_approximation" not in kw
```

====================================================================

## 5. Part B — ORCA Scan 工作流

### B1. `src/cccp/qc/interfaces/orca.py` — scan() 接口方法

#### B1.1 calc_type_map 扩展

**位置**: line 440-446。

```python
calc_type_map = {
    "opt": "Opt",
    "freq": "Freq",
    "sp": "SP",
    "optfreq": "Opt Freq",
    "nmr": "NMR",
    "scan": "Opt",      # 新增:scan 复用 Opt 路由
}
```

#### B1.2 `_build_input_blocks()` 新增 scan_defs 形参

**位置**: 方法签名(line 387)加形参 `scan_defs: list[dict] | None = None`。

在 `%geom` 块构建段(line 561-571),`is_opt_route` 为 True 时,若 `scan_defs` 非空,插入 Scan 块:

```python
if is_opt_route:
    blocks.append("%geom")
    if scan_defs:
        blocks.append("  Scan")
        for sd in scan_defs:
            sd_type = sd["type"].upper()              # B / A / D
            atoms = " ".join(str(a) for a in sd["atoms"])
            blocks.append(
                f"    {sd_type} {atoms} = {sd['start']}, {sd['stop']}, {sd['steps']}"
            )
        blocks.append("  end")
    if resolution.interval > 0:
        blocks.append(f"  Recalc_Hess {resolution.interval}")
    if geom_maxiter is not None and geom_maxiter > 0:
        blocks.append(f"  MaxIter {int(geom_maxiter)}")
    blocks.append("end")
```

> `is_opt_route` 判断(line 554)需扩展:`route.split()[0] in ("Opt", "OptTS")`,以同时覆盖 scan(Opt)与 optts(OptTS)。详见 §C1.2。

#### B1.3 新增 `_parse_scan_energies()` 解析器

```python
_SCAN_SECTION_HEADER = "RELAXED SURFACE SCAN"
_SCAN_ROW_RE = re.compile(
    r"^\s*\d+\s+(\d+)\s+([-+]?\d+\.\d+)\s+([-+]?\d+\.\d+)", re.MULTILINE
)

def _parse_scan_energies(output_file: Path) -> list[dict]:
    """解析弛豫扫描各点能量。

    Returns:
        [{"point": int, "coord": float, "energy_hartree": float}, ...]
    """
    content = Path(output_file).read_text(encoding="utf-8", errors="replace")
    sections = content.split(_SCAN_SECTION_HEADER)
    if len(sections) < 2:
        return []
    return [
        {"point": int(m.group(1)), "coord": float(m.group(2)),
         "energy_hartree": float(m.group(3))}
        for m in _SCAN_ROW_RE.finditer(sections[-1])
    ]
```

#### B1.4 新增 `scan()` 方法

**位置**: 仿 `optimize()`(line 734),在其后新增。签名:

```python
def scan(
    self,
    coordinates: np.ndarray,
    symbols: list[str],
    charge: int = 0,
    multiplicity: int = 1,
    output_dir: Path = None,
    output_name: str = "scan",
    method: str = None,
    basis: str = None,
    scan_defs: list[dict] | None = None,
    geom_maxiter: int = None,
    **kwargs,
) -> QCResult:
```

逻辑:写输入(传 `calc_type="scan"` + `scan_defs`)→ `_run_orca` → 解析能量(`_parse_scan_energies`)+ 坐标。结果放 `QCResult.metadata["scan_points"]`。

### B2. `src/acp/backends/orca.py` — scan() 转发

**位置**: 仿 `optimize()`(line 45-64),新增 `scan()` 方法转发到 `self._interface.scan(...)`,经 `to_qc_result`。

### B3. `src/acp/workflows/simple.py` — run_scan()

#### B3.1 _STAGE_NAMES 扩展

**位置**: line 30-37。

```python
_STAGE_NAMES: dict[str, list[str]] = {
    "singlepoint": ["single_point"],
    "optimize": ["optimize"],
    "frequency": ["frequency"],
    "optfreq": ["opt_freq"],
    "optfreqsp": ["opt_freq", "single_point", "shermo"],
    "xtb_optimize": ["xtb_optimize"],
    "scan": ["scan"],                # 新增
    "optts": ["ts_optimize"],        # 新增(Part C)
}
```

#### B3.2 新增 run_scan()

**位置**: 仿 `run_optimize()`(line 310)。

```python
def run_scan(
    input_source: str,
    output_dir: str | Path = "./scan_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    cfg = load_config(overrides=config) if config else load_config()
    out = _resolve_output_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name)
    calc_dir = _calc_subdir(out, name, input_source, "scan")
    state = _init_state(calc_dir, "scan", input_source)

    raw = method_kwargs or {}
    scan_defs = raw.pop("scan_defs", None)
    if not scan_defs:
        return WorkflowResult(status="failed",
                              error="scan workflow requires scan_defs")
    kwargs = _build_method_kwargs(raw)
    backend = _build_backend(cfg)
    state.set_stage("scan")
    result = backend.scan(coords, symbols, charge=chg, multiplicity=mult,
                          output_dir=calc_dir, scan_defs=scan_defs, **kwargs)
    if not result.success:
        state.fail_stage("scan", result.error_message or "Scan failed")
        return WorkflowResult(status="failed", error=result.error_message or "Scan failed")
    state.complete_stage("scan")

    scan_points = (result.metadata or {}).get("scan_points", [])
    if scan_points:
        (calc_dir / "scan_energies.json").write_text(
            json.dumps(scan_points, indent=2), encoding="utf-8")
    state.mark_completed()
    return WorkflowResult(
        status="completed",
        metadata={"output_dir": str(calc_dir), "n_points": len(scan_points),
                  "scan_points": scan_points},
    )
```

#### B3.3 `__all__` 更新

**位置**: line 573-579,追加 `"run_scan"`, `"run_optts"`。

### B4. `src/acp/catalog.py` — scan catalog + schema + 字段

#### B4.1 WORKFLOW_CATALOG 新增

**位置**: `xtb_optimize` 条目后(line ~82)。

```python
{
    "id": "scan",
    "label": "Relaxed Surface Scan",
    "label_zh": "弛豫表面扫描",
    "category": "simple",
    "description": "ORCA relaxed PES scan along bond/angle/dihedral",
    "method_schema_id": "dft_scan",
    "default_backend": "orca",
    "requires_binaries": ["orca"],
    "status": "active",
    "visible": True,
},
```

#### B4.2 METHOD_SCHEMAS["dft_scan"] 新增

**位置**: `dft_optfreqsp` 之后(line ~1228)。

```python
"dft_scan": {
    "method_levels": [
        {
            "level_id": "scan",
            "label": "Surface Scan",
            "label_zh": "表面扫描",
            "required": True,
            "allowed_engines": ["orca"],
            "fields": [
                "scan_type", "scan_atoms", "scan_start", "scan_stop", "scan_steps",
                "functional", "basis", "dispersion", "ri_approximation",
                "aux_j_basis", "aux_c_basis", "solvent_model", "solvent",
                "grid", "scf_convergence", "max_steps", "opt_convergence",
                "recalc_hess",
            ],
        }
    ],
    "profiles": [],
},
```

#### B4.3 FIELD_DEFINITIONS 新增 scan 字段

**位置**: `FIELD_DEFINITIONS` 末尾(line ~801 前)。

```python
"scan_type": {
    "type": "select", "label": "Scan Coordinate", "label_zh": "扫描坐标",
    "options": ["B", "A", "D"],
    "default": {"*": "B"},
    "help": "B=bond(2 atoms), A=angle(3 atoms), D=dihedral(4 atoms)",
},
"scan_atoms": {
    "type": "string", "label": "Atom Indices", "label_zh": "原子索引",
    "default": {"*": "0 1"},
    "help": "Space-separated 0-based atom indices (2 for bond, 3 for angle, 4 for dihedral)",
},
"scan_start": {"type": "float", "label": "Start", "label_zh": "起始值", "min": 0, "default": {"*": 1.0}},
"scan_stop":  {"type": "float", "label": "Stop",  "label_zh": "终止值", "min": 0, "default": {"*": 2.0}},
"scan_steps": {"type": "int",   "label": "Steps", "label_zh": "步数",   "min": 2, "default": {"*": 10}},
```

> 注:FIELD_DEFINITIONS 目前无 `type: "string"`,需在 `buildFieldRow`(前端)确认 string 类型走文本输入框;若前端不支持,临时用 `type: "select"` + `supports_custom: true`。见 §E4。

### B5. `src/acp/workflows/registry.py` — scan 注册

**位置**: `_WORKFLOW_REGISTRY`(line 103 后)。

```python
"scan": WorkflowRegistryEntry(
    name="scan", label="Relaxed Surface Scan",
    description="ORCA relaxed PES scan along bond/angle/dihedral.",
    requires_binaries=["orca"],
),
```

### B6. `src/acp/workflows/__init__.py` — 懒加载导出

**位置**: `__all__`(line 27-31)与 `_LAZY_SOURCES`(line 41-45)。

```python
__all__ = [..., "run_singlepoint", "run_optimize", "run_frequency",
           "run_optfreq", "run_optfreqsp", "run_scan", "run_optts"]

_LAZY_SOURCES = {
    ...,
    "run_scan": "acp.workflows.simple",
    "run_optts": "acp.workflows.simple",
    ...
}
```

### B7. `src/acp/cli.py` — scan 子命令

#### B7.1 parser 注册

**位置**: `_add_simple_workflow_parsers()`(line 122)的循环列表追加:

```python
("scan", "Surface Scan", "Run ORCA relaxed surface scan",
 "Examples:\n  acp run scan --input mol.xyz --scan-type B --scan-atoms 0 1 --scan-start 1.0 --scan-stop 2.0 --scan-steps 10"),
("optts", "TS Optimization", "Run ORCA transition state optimization (OptTS)",
 "Examples:\n  acp run optts --input ts_guess.xyz --inhess read"),
```

#### B7.2 参数注册

**位置**: `_add_simple_workflow_args()`(line 156)。在 opt 类参数分支(line 186 `if wf in ("optimize", "optfreq", "optfreqsp")`)扩展为含 scan/optts:

```python
if wf in ("optimize", "optfreq", "optfreqsp", "scan", "optts"):
    parser.add_argument("--geom-maxiter", ...)
    parser.add_argument("--opt-convergence", ...)
    # calc-hess 互斥组(同现有)
    ...

if wf == "scan":
    parser.add_argument("--scan-type", default="B", choices=["B", "A", "D"],
                        help="Scan coordinate type: B=bond, A=angle, D=dihedral")
    parser.add_argument("--scan-atoms", required=True,
                        help="Space-separated 0-based atom indices (e.g. '0 1')")
    parser.add_argument("--scan-start", type=float, required=True, help="Start value (Å or °)")
    parser.add_argument("--scan-stop", type=float, required=True, help="Stop value (Å or °)")
    parser.add_argument("--scan-steps", type=int, default=10, help="Number of scan points")

if wf == "optts":
    parser.add_argument("--inhess", default="none", choices=["none", "read"],
                        help="Initial Hessian: none (default) or read (read <name>.hess)")
```

#### B7.3 handler

**位置**: 仿 `_handle_optimize()`(line 1125),新增 `_handle_scan()`:

```python
def _handle_scan(args: argparse.Namespace) -> int:
    from acp.workflows.simple import run_scan
    setup_logging(args.log_level)
    cfg = _build_config(args)
    atoms = [int(x) for x in args.scan_atoms.split()]
    scan_defs = [{"type": args.scan_type, "atoms": atoms,
                  "start": args.scan_start, "stop": args.scan_stop,
                  "steps": args.scan_steps}]
    method_kwargs = _build_simple_method_kwargs(args)
    method_kwargs["scan_defs"] = scan_defs
    try:
        result = run_scan(input_source=args.input, output_dir=Path(args.output),
                          config=cfg, charge=args.charge, multiplicity=args.multiplicity,
                          name=args.name, method_kwargs=method_kwargs)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.exception("Scan failed: %s", exc)
        return 1
    if result.status == "completed":
        logger.info("Surface scan completed: %s points", result.metadata.get("n_points"))
        return 0
    logger.error("Scan failed: %s", result.error)
    return 1
```

#### B7.4 dispatch 注册

**位置**: `dispatch` dict(line 1986)。

```python
"scan": _handle_scan,
"optts": _handle_optts,
```

### B8. `src/acp/scheduler/` — scan 调度接线(4 文件)

#### B8.1 jobs.py fallback 列表

**位置**: `_derive_supported_workflows()` 的 fallback(line 68-72)。

```python
return (
    "ensemble", "energy", "mechanism",
    "singlepoint", "optimize", "frequency", "optfreq", "optfreqsp",
    "scan", "optts",                                          # 新增
    "fake",
)
```

> 正常情况由 catalog `status=="active"` 自动派生,fallback 仅在 catalog 不可导入时用。

#### B8.2 stage_tasks.py plan provider

**位置**: line 540 后。

```python
class _ScanStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan(stage_name="scan")]

class _OpttsStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan(stage_name="ts_optimize")]

register_plan_provider("scan", _ScanStagePlanProvider())
register_plan_provider("optts", _OpttsStagePlanProvider())
```

#### B8.3 runner.py `_build_cmd` whitelist + 分支

**位置 1**: whitelist 元组(line 707-719)追加 `"scan", "optts"`。

**位置 2**: simple 分支(line 788)扩展:

```python
elif wf in ("singlepoint", "optimize", "frequency", "optfreq", "optfreqsp", "scan", "optts"):
    cmd += ["--input", str(source), "--output", str(work_dir)]
    if spec.name:
        cmd += ["--name", spec.name]
    levels = method.get("levels", {})
    if levels:
        from acp.catalog import method_levels_to_cli_flags
        if wf == "optfreqsp":
            prefix_map = {"optfreq": "", "single_point": "sp-", "thermo": ""}
            cmd += method_levels_to_cli_flags(levels, prefix_map)
        else:
            cmd += method_levels_to_cli_flags(levels)
```

> scan/optts 的专用字段(scan_type 等)通过 `_LEVEL_TO_CLI_FLAG_MAP`(§B8.5)自动转换,无需在此分支手写。

#### B8.4 remote/script_gen.py whitelist + 分支

**位置**: 与 runner.py 完全对称(line 132-143 whitelist + line 211 分支)。**E7 parity 原则**: runner 与 script_gen 必须一致,由 `tests/test_acp_xtbmd_platform_phase5.py` 风格的测试守护。

#### B8.5 catalog.py `_LEVEL_TO_CLI_FLAG_MAP` 追加

**位置**: line 2440。

```python
_LEVEL_TO_CLI_FLAG_MAP: dict[str, str] = {
    ...,
    # scan / optts 专用字段
    "scan_type": "scan-type",
    "scan_atoms": "scan-atoms",
    "scan_start": "scan-start",
    "scan_stop": "scan-stop",
    "scan_steps": "scan-steps",
    "inhess": "inhess",
}
```

> `scan_atoms` 是列表/字符串,需确认 `method_levels_to_cli_flags`(line 2503)的 `str(value)` 能正确序列化(空格分隔字符串可直接传;若前端发数组则需 join)。建议前端统一发空格分隔字符串。

====================================================================

## 6. Part C — ORCA OptTS 工作流

> 与 Part B 模式高度相似,以下仅列差异点。

### C1. `src/cccp/qc/interfaces/orca.py` — ts_optimize() 接口方法

#### C1.1 calc_type_map 扩展

```python
calc_type_map = {
    ...,
    "optts": "OptTS",   # 新增
}
```

#### C1.2 `is_opt_route` 判断扩展

**位置**: line 554。

```python
# 现状: is_opt_route = route.split()[0] == "Opt"
is_opt_route = route.split()[0] in ("Opt", "OptTS")
```

> 这样 OptTS 也会生成 `%geom` 块(Recalc_Hess / inhess / MaxIter),符合 TS 优化需求。

#### C1.3 inhess 支持

**位置**: `_build_input_blocks()` 签名加 `inhess: str | None = None`;在 `%geom` 块内:

```python
if is_opt_route:
    blocks.append("%geom")
    if inhess == "read":
        blocks.append("  inhess read")
    if scan_defs:  # Part B
        ...
    ...
```

#### C1.4 新增 ts_optimize() 方法

**位置**: 仿 `optimize()`(line 734)。calc_type 传 `"optts"`,转发 `inhess` 参数。坐标/能量解析复用 `LogParser.extract_last_converged_coords` / `extract_energy`。

### C2. `src/acp/backends/orca.py` — ts_optimize() 转发

仿 `optimize()`(line 45)新增 `ts_optimize()`。

### C3. `src/acp/workflows/simple.py` — run_optts()

**位置**: 仿 `run_optimize()`(line 310)。`_STAGE_NAMES["optts"] = ["ts_optimize"]`(§B3.1 已加)。

```python
def run_optts(
    input_source: str,
    output_dir: str | Path = "./optts_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    # ... 仿 run_optimize,调 backend.ts_optimize(...)
    # 额外:从 method_kwargs 提取 inhess 传入
    # 写 ts_optimized.xyz + energy.json
```

> **设计决策**: optts 仅做 TS 优化,不做内嵌频率验证(单一职责,与 optfreq 分离)。用户可后续 `acp run frequency --input ts_optimized.xyz` 验证虚频。

### C4. `src/acp/catalog.py` — optts catalog + schema + 字段

#### C4.1 WORKFLOW_CATALOG 新增

```python
{
    "id": "optts",
    "label": "Transition State Optimization",
    "label_zh": "过渡态优化",
    "category": "simple",
    "description": "ORCA OptTS saddle-point optimization",
    "method_schema_id": "dft_optts",
    "default_backend": "orca",
    "requires_binaries": ["orca"],
    "status": "active",
    "visible": True,
},
```

#### C4.2 METHOD_SCHEMAS["dft_optts"] 新增

```python
"dft_optts": {
    "method_levels": [
        {
            "level_id": "ts_optimize",
            "label": "TS Optimization",
            "label_zh": "过渡态优化",
            "required": True,
            "allowed_engines": ["orca"],
            "fields": [
                "inhess",
                "functional", "basis", "dispersion", "ri_approximation",
                "aux_j_basis", "aux_c_basis", "solvent_model", "solvent",
                "grid", "scf_convergence", "max_steps", "opt_convergence",
                "recalc_hess",
            ],
        }
    ],
    "profiles": [],
},
```

#### C4.3 FIELD_DEFINITIONS["inhess"] 新增

```python
"inhess": {
    "type": "select", "label": "Initial Hessian", "label_zh": "初始 Hessian",
    "options": ["none", "read"],
    "default": {"*": "none"},
    "help": "read = read precomputed <name>.hess for TS optimization",
},
```

### C5. `src/acp/workflows/registry.py`

```python
"optts": WorkflowRegistryEntry(
    name="optts", label="Transition State Optimization",
    description="ORCA OptTS saddle-point optimization.",
    requires_binaries=["orca"],
),
```

### C6. `src/acp/workflows/__init__.py`

已在 §B6 一起加入 `"run_optts"`。

### C7. `src/acp/cli.py`

- parser 注册(§B7.1 已含 optts)。
- 参数注册(§B7.2 `--inhess` 已含)。
- handler `_handle_optts()`(仿 `_handle_optimize`)。
- dispatch(§B7.4 已含)。

### C8. scheduler 接线

已在 §B8(scan 的 4 文件改动)中一并加入 optts(whitelist + plan provider + 分支)。

====================================================================

## 7. Part E — Web 前端(`frontend/ACP_Workbench_v2.html`)

### E1. GFN 字段隐藏(核心前端改动)

**位置**: `buildFieldRow()`(line 4908)。

**现状**: 选中 functional 后,basis 下拉从 `functional_options_map[functional].basis` 取选项。GFN 方法此列表为空,会渲染出空/Custom-only basis 框。

**改动**: 在 `buildFieldRow` 的 RI/aux 不可变徽章逻辑(line 4930-4948)之后,加 GFN 家族隐藏:

```js
var isGfn = funcMeta && (funcMeta.family === "gfn" || funcMeta.family === "gfnff");
if (isGfn && ["basis", "aux_j_basis", "aux_c_basis", "ri_approximation", "grid", "dispersion"].indexOf(fieldName) >= 0) {
    renderRiImmutableBadge(fDiv, "gfn");   // 复用现有徽章组件
    getLevelState(lvDef.level_id)[fieldName] = "";
    dest.appendChild(fDiv);
    return;
}
```

> 复用 `renderRiImmutableBadge`(已存在,用于 composite/automatic RI 方法),传入标识符 `"gfn"`。需确认该函数接受任意标识符字符串;若硬编码 RI 关键字,则新增 `renderGfnBadge` 或泛化之。

**functional change 联动**(line 5055 handler):在 `ri_support !== "user"` 分支(line 5093)旁加:

```js
var liveFamily = (liveFuncMeta && liveFuncMeta.family) || "";
if (liveFamily.startsWith("gfn")) {
    st.basis = ""; st.aux_j_basis = ""; st.aux_c_basis = "";
    st.ri_approximation = "none"; st.grid = ""; st.dispersion = "none";
}
```

### E2. ALPB 溶剂模型 — 无前端改动

`solvent_model` 下拉由 `FIELD_DEFINITIONS["solvent_model"].per_backend["orca"]` 驱动(line 4159)。§A2.3 加 `"ALPB"` 后自动出现。✅

### E3. scan / optts 工作流卡片 — 无前端改动

`renderWorkflowCategories`(line 4670)遍历 catalog,按 `category:"simple"` 分组。§B4.1/C4.1 加条目后,卡片自动出现在"简单计算"组下。✅

### E4. scan / optts 字段渲染 — 需确认 string 类型支持

`scan_atoms` 是 `type: "string"`。当前 `buildFieldRow` 的渲染器(line 5020+)处理 select / number / multi,但 **string 类型可能未覆盖**。

**验证步骤**: 检查 `buildFieldRow` 是否有 `else` 分支渲染文本输入框。若无,二选一:
- **方案 1**(推荐): 在 `buildFieldRow` 加 `type === "string"` 分支,渲染 `<input type="text">`。
- **方案 2**: `scan_atoms` 改用 `type: "select"` + `supports_custom: true`,用户手填原子索引。

`scan_type`(select B/A/D)、`scan_start/stop`(float)、`scan_steps`(int)、`inhess`(select)均由现有渲染器覆盖。✅

### E5. 字段流向闭环(前端 ⇄ 后端 parity)

**提交路径**:
```
前端 cleanedLevels.scan = {scan_type, scan_atoms, scan_start, ...}
  → method.levels (submitJobModal line 5929)
  → runner._build_cmd scan 分支
  → method_levels_to_cli_flags(levels) (经 _LEVEL_TO_CLI_FLAG_MAP 转换)
  → ["--scan-type","B","--scan-atoms","0 1",...]
```

**前提**: §B8.5 已把 scan_*/inhess 加入 `_LEVEL_TO_CLI_FLAG_MAP`。前端 `submitJobModal`(line 5929-5937)把 `wizardState.method.stages` 清理成 `levels` 整体发送,**无需前端提交逻辑改动**。✅

### E6. i18n 标签 — catalog 内 label_zh 即可

- 字段标签: `buildFieldRow`(line 4920)读 `FIELD_DEFINITIONS[name].label_zh`,**不**走 `I18N_ZH`/`I18N_EN` 表。
- 工作流卡片标签: `renderWorkflowCategories`(line 4694)读 `wf.label_zh`。
- 故所有新标签在 catalog 里写好 `label`/`label_zh` 即可,无需改前端 i18n 表。

### E7. v1 工作台 — 无需改动

`ACP_Workbench.html`(558 行)是轻量仪表盘,工作流列表从 `/api/workflows` 拉取(line 311),无方法配置向导。✅

====================================================================

## 8. Part D — 测试与文档

### D1. 测试用例

**文件**: 扩展 `tests/test_acp_workflows_simple.py`。

| 测试 | Part | 断言要点 |
|------|------|----------|
| `test_method_meta_gfn_entries` | A | 4 个 GFN 条目 basis_inline=False, basis=(), family 以 gfn 开头 |
| `test_functional_options_map_gfn_basis_empty` | A | GFN2-xTB 的 basis 列表为空 |
| `test_orca_input_blocks_gfn_no_basis` | A | GFN2-xTB 路由无 basis / %basis 块 |
| `test_orca_input_blocks_gfn_alpb_solvent` | A | ALPB( 在路由, %cpcm 不出现 |
| `test_build_method_kwargs_gfn_strips_basis` | A | GFN method 时 basis/ri 字段被清 |
| `test_scan_optts_in_catalog_and_active` | B/C | scan/optts 在 WORKFLOW_CATALOG 且 status=active |
| `test_orca_input_blocks_scan_geom_block` | B | scan_defs 生成 `%geom Scan ... end` |
| `test_orca_input_blocks_scan_bond_format` | B | `B 0 1 = 1.0, 2.0, 10` 格式正确 |
| `test_parse_scan_energies` | B | 从模拟输出解析点-能量表 |
| `test_run_scan_mock` | B | mock backend.scan,断言 scan_energies.json 写出 |
| `test_run_scan_no_scan_defs_fails` | B | 缺 scan_defs 时返回 failed |
| `test_orca_input_blocks_optts_route` | C | OptTS 路由 + %geom 块生成 |
| `test_orca_input_blocks_optts_inhess_read` | C | inhess=read 生成 `inhess read` |
| `test_run_optts_mock` | C | mock backend.ts_optimize |
| `test_cli_help_contains_scan_flags` | B | `acp run scan --help` 含 --scan-type 等 |
| `test_cli_help_contains_optts_flags` | C | `acp run optts --help` 含 --inhess |
| `test_level_to_cli_flag_map_scan_optts` | B/C | scan_*/inhess 在映射表 |
| `test_runner_script_gen_scan_optts_whitelist` | B/C | runner 与 script_gen 的 whitelist 含 scan/optts( parity) |

### D2. API 层测试(前端数据源)

**新建**: `tests/test_acp_api_scan_optts_gfn.py`

```python
def test_api_workflows_includes_scan_optts(client):
    resp = client.get("/api/v1/workflows")
    ids = [w["id"] for w in resp.json()["workflows"]]
    assert "scan" in ids and "optts" in ids

def test_api_method_meta_includes_gfn(client):
    resp = client.get("/api/v1/methods")
    meta = resp.json()["method_meta"]
    assert "GFN2-xTB" in meta
    assert meta["GFN2-xTB"]["family"].startswith("gfn")
    fmap = resp.json()["functional_options_map"]
    assert fmap["GFN2-xTB"]["basis"] == []
```

### D3. AGENTS.md 文档更新

| 文件 | 更新点 |
|------|--------|
| `AGENTS.md`(根) | simple 工作流列表加 scan/optts;WHERE TO LOOK 加 scan/optts |
| `src/acp/AGENTS.md` | STRUCTURE 的 simple 描述;WHERE TO LOOK simple 行 |
| `src/acp/workflows/AGENTS.md` | simple.py 描述加 run_scan/run_optts;STRUCTURE 行数 |
| `src/acp/backends/AGENTS.md` | ORCA 行加 scan/ts_optimize 委托 |
| `src/cccp/qc/interfaces/AGENTS.md` | orca.py 行加 scan/optts/ALPB |
| `src/acp/scheduler/AGENTS.md` | jobs.py helpers 行提 scan/optts(若加专用 flag builder) |

====================================================================

## 9. 实施顺序与里程碑

### 里程碑 M1:GFN 全栈(Part A + E1)— 可独立联调

| 步骤 | 文件 | 验证 |
|------|------|------|
| 1 | `catalog.py` METHOD_META 加 4 GFN + functional/solvent_model 选项 | `pytest test_method_meta_gfn_entries` |
| 2 | `orca.py` ALPB 溶剂路径 | `pytest test_orca_input_blocks_gfn_*` |
| 3 | `simple.py` GFN kwargs 清洗 | `pytest test_build_method_kwargs_gfn_strips_basis` |
| 4 | `ACP_Workbench_v2.html` buildFieldRow GFN 隐藏 | 浏览器:选 GFN2-xTB,basis 行消失 |
| 5 | ruff + mypy + pytest tests/test_acp_workflows_simple.py | 全绿 |

### 里程碑 M2:Scan 全栈(Part B)— 依赖 M1 的 catalog 基础

| 步骤 | 文件 | 验证 |
|------|------|------|
| 1 | `orca.py` scan_defs 支持 + scan() + 解析器 | `pytest test_orca_input_blocks_scan_*` |
| 2 | `backends/orca.py` scan() 转发 | 单元测试 mock |
| 3 | `simple.py` run_scan() + _STAGE_NAMES | `pytest test_run_scan_mock` |
| 4 | `catalog.py` dft_scan schema + FIELD_DEFINITIONS scan_* + `_LEVEL_TO_CLI_FLAG_MAP` | schema 测试 |
| 5 | `registry.py` + `__init__.py` | 导入测试 |
| 6 | `cli.py` parser + handler + dispatch | `acp run scan --help` 含 flags |
| 7 | scheduler 4 文件(jobs/stage_tasks/runner/script_gen) | whitelist parity 测试 |
| 8 | ruff + mypy + pytest | 全绿 |

### 里程碑 M3:OptTS 全栈(Part C)— 模式同 M2

| 步骤 | 文件 | 验证 |
|------|------|------|
| 1 | `orca.py` OptTS calc_type + is_opt_route + inhess + ts_optimize() | `pytest test_orca_input_blocks_optts_*` |
| 2 | `backends/orca.py` ts_optimize() 转发 | 单元测试 |
| 3 | `simple.py` run_optts() | `pytest test_run_optts_mock` |
| 4 | `catalog.py` dft_optts schema + FIELD_DEFINITIONS inhess | schema 测试 |
| 5 | `registry.py` + `__init__.py` | 导入测试 |
| 6 | `cli.py` handler + dispatch | `acp run optts --help` |
| 7 | scheduler(已在 M2 step7 一起加 whitelist) | parity 测试 |
| 8 | ruff + mypy + pytest | 全绿 |

### 里程碑 M4:文档 + 收尾(Part D)

| 步骤 | 文件 |
|------|------|
| 1 | 6 个 AGENTS.md 更新 |
| 2 | API 测试 `test_acp_api_scan_optts_gfn.py` |
| 3 | 端到端浏览器验证(scan/optts 提交、GFN 选方法) |

====================================================================

## 10. 验证命令

```bash
# 静态检查
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/

# 单元测试
pytest tests/test_acp_workflows_simple.py -v
pytest tests/test_acp_api_scan_optts_gfn.py -v

# CLI 烟雾测试
python -m acp.cli run scan --help
python -m acp.cli run optts --help
python -m acp.cli run singlepoint --help    # 确认 GFN 不破坏现有

# 服务重启(改代码后)
sudo systemctl restart acp
```

====================================================================

## 11. 风险与开放问题

### 11.1 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| ORCA 版本差异(GFN-FF 需 5.0+) | GFN-FF 在旧版 ORCA 失败 | 文档注明最低版本;is_available 检测 |
| ALPB 溶剂名与 SMD 溶剂名映射不一致 | GFN 溶剂计算失败 | 复用 `orca_smd_solvent()` 前核对 ORCA ALPB 支持的溶剂表 |
| 前端 `renderRiImmutableBadge` 硬编码 RI 标识 | GFN 徽章显示异常 | 预检函数签名,必要时新增 `renderGfnBadge` |
| `scan_atoms` string 类型前端未支持 | 扫描卡片无法填原子索引 | §E4 方案 1/2 二选一 |
| optts 无内嵌虚频验证 | 用户忘验证,误判 TS | 文档强调;未来可加 optts+freq 组合工作流 |

### 11.2 开放问题(待确认)

1. **scan 多坐标**:首版仅单坐标(可多行顺序扫描)。是否需要"网格扫描"(两变量笛卡尔积)?— 暂不,后端 `scan_defs` 列表已预留。
2. **optts Hessian 文件来源**:`inhess read` 需用户提供 `<name>.hess`。是否加 `--hess-file <path>` 显式指定?— 首版用同名约定,未来增强。
3. **GFN 在 energy/ensemble 工作流**:GFN 目前仅在 simple 工作流可用。是否让 CENSO/energy 的 DFT level 也支持 GFN?— 暂不,避免 CENSO 路径复杂化。

====================================================================

## 附录 A:文件改动速查表

```
src/cccp/qc/interfaces/orca.py          [A1, B1, C1]  ALPB + scan() + ts_optimize()
src/acp/backends/orca.py                [B2, C2]      scan() + ts_optimize() 转发
src/acp/workflows/simple.py             [A3, B3, C3]  GFN kwargs + run_scan + run_optts
src/acp/workflows/__init__.py           [B6, C6]      懒加载导出
src/acp/workflows/registry.py           [B5, C5]      注册条目
src/acp/catalog.py                      [A2, B4, C4, B8.5]  METHOD_META + schemas + fields + flag map
src/acp/cli.py                          [B7, C7]      parser + handler + dispatch
src/acp/scheduler/jobs.py               [B8.1]        fallback 列表
src/acp/scheduler/stage_tasks.py        [B8.2]        plan providers
src/acp/scheduler/runner.py             [B8.3]        whitelist + 分支
src/acp/scheduler/remote/script_gen.py  [B8.4]        whitelist + 分支(parity)
frontend/ACP_Workbench_v2.html          [E1, E4]      GFN 隐藏 + string 字段支持
tests/test_acp_workflows_simple.py      [A4, B/D1, C] 测试
tests/test_acp_api_scan_optts_gfn.py    [D2]          API 测试(新建)
docs/ACP_Scan_OptTS_GFN_DevPlan.md      [本文档]
6 × AGENTS.md                           [D3]          文档同步
```

====================================================================

**结束**
