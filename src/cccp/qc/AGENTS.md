# qc/ — Quantum Chemistry Module

## OVERVIEW
Quantum chemistry subprocess wrappers, thermochemistry/cluster runners and the shared scientific adapters consumed by the calculation and backend layers. 25 `.py`: 8 top-level modules (7 shared adapters + `__init__.py`) + 3 subpackages — `interfaces/` (15 modules: ORCA/CREST/xTB/CENSO/ISOSTAT/Molclus execution + pure render/parse helpers), `runners/` (Shermo thermochemistry, whole module in `__init__.py`), `cluster/` (LSF/Local adapters + factory, whole module in `__init__.py`). 2026-08-02 consolidation: censo/isostat/molclus interfaces moved from `acp.backends` into `interfaces/` — cccp is the single subprocess layer.

## STRUCTURE
```
qc/
├── __init__.py         # Re-exports 8 interface names + run_shermo/batch_process_thermo + cluster adapters
├── keyword_registry.py # Method family / implementation / keyword canonicalization / applicability — logic only
├── method_meta.py      # METHOD_META calculation semantics + derived registry fields — single source of truth
├── resolved_spec.py    # ResolvedCalculationSpec — single parameter-resolution rule set (basis/dispersion/RI/aux/SCF)
├── hessian_policy.py   # Recalc_Hess graded defaults — element classification + resolve_recalc_hess
├── shermo_adapter.py   # Shared low-level Shermo execution adapter (execute_shermo)
├── thermo_normalize.py # Pure Shermo result normalization (no file/process I/O)
├── translation.py      # Structured spec → ORCA `!` route/%geom + CENSO template lines
├── interfaces/         # 15 modules — QC wrappers + pure helper modules (see interfaces/AGENTS.md)
├── runners/            # run_shermo + batch_process_thermo — all implemented in __init__.py
└── cluster/            # LSF + Local adapters + factory — all implemented in __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ABC contract | `interfaces/base.py` | `QCInterfaceBase`, `QCResult` dataclass, `to_qc_result()` |
| ORCA execution | `interfaces/orca.py` | opt / constrained_optimize / relaxed_scan / SP / frequency / casscf / nmr_shielding / transition_state_opt / irc |
| ORCA TS/IRC helpers | `interfaces/orca_ts.py` | 861 L pure input+parse (`ts_opt_route`, `irc_route`, `parse_ts_mode_vectors`, `parse_irc_endpoints`); no subprocess |
| ORCA `!` route rendering | `interfaces/route_render.py` | **single renderer** — `render_route_line`/`RouteKeyword`/`orca_keyword_context`; no other module hand-assembles ORCA route tokens |
| Reaction-coordinate constraints | `interfaces/constraints.py` | `CoordinateSpec`/`ReactionCoordinatePlan`/`orca_constraint_block` (ORCA 0-based; xTB 1-based writer lives in `xtb_scan`) |
| CREST conformer search | `interfaces/crest.py` | `run_conformer_search()` single-stage; `run_two_stage_search` removed in Phase C |
| CREST batch optimization | `interfaces/crest.py` | `run_batch_optimization()` (-mdopt); retained for upstream compatibility |
| xTB opt/SP/scan/thermo | `interfaces/xtb.py` | Standalone since Phase C (split from crest.py); `optimize`/`constrained_optimize`/`relaxed_scan`/`single_point`/`enso_thermo` |
| xTB path search | `interfaces/xtb_path.py` | `XTBPathInterface.path_search()`, `PathSearchResult`, `path_search()` wrapper |
| xTB relaxed scan | `interfaces/xtb_scan.py` | `xcontrol_constraint_block()` (1-based `$constrain`), `RelaxedScanPoint`/`RelaxedScanResult` |
| xTB thermo | `interfaces/xtb_thermo.py` | `run_xtb_enso()` + `XTBThermoResult` (`--bhess --enso` SPH/mRRHO) |
| ORCA `.hess` reader | `interfaces/hess_file.py` | `parse_orca_hess_file()` → `HessFileData` (feeds tsmode / mode mapping) |
| CENSO refinement | `interfaces/censo.py` | `CensoInterface` — rcfile gen, presets, template injection, JSON/XYZ parsing, env pinning (single subprocess layer; 2026-08-02) |
| ISOSTAT clustering | `interfaces/isostat.py` | `IsostatInterface` — exit-24 title normalisation, error classification, env pinning (single ISOSTAT path; 2026-08-02) |
| Molclus pipeline | `interfaces/molclus.py` | `MolclusInterface.run_md()/search()` — md.inp/settings.ini, trajectory validation, env pinning (2026-08-02) |
| Legacy ISOSTAT | `runners/__init__.py` | DELETED — run_isostat removed in wave-8 |
| Shermo thermo | `runners/__init__.py` | `run_shermo()`（唯一 runner）+ `batch_process_thermo()`（低层兼容包装，DEPRECATED）— 见下方"Shermo 单一实现" |
| Keyword policy | `keyword_registry.py` | `method_family`/`resolve_implementation`/`resolve`/`is_applicable`/`legal_values` — keyword/value logic only, no I/O |
| Method semantics | `method_meta.py` | `METHOD_META`, `method_meta()`, `scf_constraint()`, aux-basis helpers |
| Parameter resolution | `resolved_spec.py` | `ResolvedCalculationSpec`, `resolve_calculation_spec()`, `clamp_calculation_fields()` |
| Hessian graded defaults | `hessian_policy.py` | `classify_symbols()`, `resolve_recalc_hess()`, `HessianResolution` |
| Thermochemistry normalization | `shermo_adapter.py` + `thermo_normalize.py` | `execute_shermo()` launches Shermo; `thermo_normalize` is pure (standard state/units/Gibbs metadata) |
| ORCA/CENSO rendering | `translation.py` | `render_orca_opt()`/`render_opt_geom_lines()`/`render_censo_template_lines()` — built on `route_render` + `keyword_registry` |
| LSF job submission | `cluster/__init__.py` | `LSFClusterAdapter` (placeholder — site-specific config required) |
| Local execution | `cluster/__init__.py` | `LocalClusterAdapter` |
| Cluster factory | `cluster/__init__.py` | `create_cluster_adapter()` |

## CONVENTIONS
- `QCResult` dataclass is universal return type across all interface files
- `ORCAInterface` inherits from `QCInterfaceBase` ABC; `CRESTInterface`, `XTBInterface`, `XTBPathInterface`, `CensoInterface`, `IsostatInterface`, `MolclusInterface` do NOT (upstream form, intentional)
- **Pure helper boundary**: only the 8 subprocess-capable modules (`orca`, `crest`, `xtb`, `xtb_path`, `xtb_thermo`, `censo`, `isostat`, `molclus`) may spawn binaries; `constraints`, `hess_file`, `orca_ts`, `route_render`, `xtb_scan` are pure data/render/parse (no subprocess, no executable resolution)
- **Thread env pinning**: every QC subprocess pins `OMP/MKL/OPENBLAS_NUM_THREADS` (LSF injects node-wide `OMP_NUM_THREADS`); `xtb`/`isostat`/`molclus` use a `_thread_env()` helper (`xtb_path` reuses `xtb._thread_env`), `crest`/`xtb_thermo`/`censo` pin inline env copies (censo pins its children to `omp-min`, not nproc), orca relies on `%pal nprocs` (partial — audit §3.3)
- **Coordinate indexing is engine-specific**: ORCA `%geom` constraints are 0-based (`orca_constraint_block` writes atoms verbatim); xTB xcontrol `$constrain` is 1-based (`xcontrol_constraint_block` converts) — never share raw indices across engines
- **Single ORCA route renderer**: every `!`-line assembly point (`ORCAInterface._build_input_blocks`, `casscf`, `_write_nmr_input`, `orca_ts.ts_opt_route`/`irc_route`, `translation.render_orca_opt`) funnels through `interfaces/route_render.render_route_line`; do not hand-assemble registry-governed tokens
- **Subprocess ownership**: `acp/backends` must not contain subprocess logic — cccp interfaces are the single layer (governance principle, 2026-08-02)

## Shermo 单一实现（2026-10-05，plan todo 31）
- **`run_shermo` 是 QC 接口/runner 层职责** — 驻留 `runners/__init__.py` 属正常分层；目录统一（如并入 `interfaces/`）非最高优先级，迁移须保留旧导入路径。
- **入口数量 ≠ 实现数量**。两个消费入口共用唯一科学实现 `qc/shermo_adapter.py` + `qc/thermo_normalize.py`：
  - 兼容 backend 入口：`ExternalBackend.thermochemistry`（实现体 `cccp/backends/external_backend.py`，`acp/backends` 仅 re-export shim）→ 共享适配 `execute_shermo`
  - 任务入口：`run_thermochemistry`（`acp/calculations/primitives/thermochemistry.py` 兼容包装 → `cccp/calculation/tasks/thermochemistry.py`）→ 同一共享适配
- `run_shermo` 的合法调用方：共享适配（`shermo_adapter.py`）与 `batch_process_thermo` 等低层兼容包装（同一 runner 文件内，**不计为重复科学实现**）；`energy_shared` 的直调已移除。
- `scripts/check_grep_gates.py::FINAL_SHERMO_ALLOWED_PATHS` 仅 `src/cccp/**` — `src/acp` 禁止生产直呼 `run_shermo`（注释/导入/类型除外）。
- **Must NOT**：以"唯一生产消费=任务入口"为由删除兼容 backend 调用（两入口均受支持）；迁移目录引入兼容风险而不留旧导入。两入口复用同一实现由 `tests/test_thermochemistry.py`（todo 14/22）守护。

## ANTI-PATTERNS (HISTORICAL)
- ~~XTBInterface co-located in `crest.py`~~ **Fixed in Phase C (2026-07-27) — now standalone `xtb.py`**
- ~~censo/isostat/molclus subprocess logic in `acp.backends`~~ **Fixed 2026-08-02 — consolidated into `interfaces/censo.py`, `interfaces/isostat.py`, `interfaces/molclus.py`**
- **`CRESTInterface` has no base class** — `class CRESTInterface:` by design (2026-07-13 reverse-sync restored upstream form). Earlier docs claiming it inherits `QCInterfaceBase` were wrong.

## REMAINING
- **SLURM/PBS adapters unimplemented** — only LSF and Local exist
- **LSF adapter is placeholder** — labelled as such in `cluster/__init__.py` (site-specific configuration required)
