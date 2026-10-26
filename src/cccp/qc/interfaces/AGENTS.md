# qc/interfaces/ — QC Software Interfaces

## OVERVIEW
Subprocess wrappers for external quantum chemistry binaries — ORCA, CREST, xTB, CENSO, ISOSTAT, Molclus — plus the pure input-generation/render/parse helpers they share. 15 `.py`: 8 subprocess-capable modules (`orca`, `crest`, `xtb`, `xtb_path`, `xtb_thermo`, `censo`, `isostat`, `molclus`) + `base.py` (ABC/`QCResult`) + 5 pure helper modules (`constraints`, `hess_file`, `orca_ts`, `route_render`, `xtb_scan`) + `__init__.py`. Subprocess execution is confined to the 8 executable wrappers — this package is the **single subprocess layer** (governance principle since the 2026-08-02 consolidation: `acp/backends` must not own subprocess logic).

## STRUCTURE
```
interfaces/
├── __init__.py     # Re-exports 13 stable symbols from base/censo/crest/isostat/molclus/orca/xtb/xtb_path/xtb_thermo
├── base.py         # QCInterfaceBase(ABC) + QCResult dataclass + to_qc_result()
├── orca.py         # ORCAInterface — opt/constrained_optimize/relaxed_scan/SP/freq/CASSCF/NMR/TS opt/IRC (3812 L)
├── orca_ts.py      # Pure ORCA OptTS/IRC input builders + output parsers (861 L; no subprocess)
├── route_render.py # Single ORCA `!` route-line renderer — render_route_line/RouteKeyword/orca_keyword_context
├── constraints.py  # CoordinateSpec/ReactionCoordinatePlan + orca_constraint_block (ORCA 0-based indices)
├── hess_file.py    # parse_orca_hess_file() → HessFileData (ORCA .hess reader; feeds tsmode)
├── crest.py        # CRESTInterface — conformer search + batch -mdopt
├── xtb.py          # XTBInterface — optimize/constrained_optimize/relaxed_scan/single_point/enso_thermo
├── xtb_path.py     # XTBPathInterface — xTB --path metadynamics path search
├── xtb_scan.py     # xcontrol_constraint_block() (1-based $constrain) + RelaxedScanPoint/RelaxedScanResult
├── xtb_thermo.py   # xTB --bhess --enso thermo runner + XTBThermoResult
├── censo.py        # CensoInterface — rcfile gen, presets, template injection, JSON/XYZ parsing (2026-08-02)
├── isostat.py      # IsostatInterface — cluster() with exit-24 title normalisation (2026-08-02)
└── molclus.py      # MolclusInterface — run_md()/search() xTB-MD pipeline (2026-08-02)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC contract | `base.py` | `QCInterfaceBase`, `QCResult` dataclass, `to_qc_result()` |
| ORCA execution | `orca.py` | `ORCAInterface` — optimize/constrained_optimize/relaxed_scan/single_point/frequency/casscf/nmr_shielding/transition_state_opt/irc |
| ORCA TS/IRC helpers | `orca_ts.py` | Pure builders `ts_opt_route`/`ts_geom_block`/`freq_block_for_ts`/`irc_route`/`irc_block` + parsers `parse_ts_mode_vectors`/`parse_ts_frequencies`/`parse_irc_endpoints`/`resolve_irc_ts_energy`; consumers include `acp/calculations/tsmode/engine.py`, `cccp/calculation/tasks/irc.py`, `acp/results/irc_projection.py`/`structure_viewer.py` |
| ORCA `!` route line | `route_render.py` | **Single renderer** `render_route_line()` + `RouteKeyword` + `orca_keyword_context()` + `orca_gfn_solvent_token()`; registry-governed tokens, GFN-inapplicable DFT params stripped at every entry point |
| ORCA constraints | `constraints.py` | `CoordinateSpec`/`ReactionCoordinatePlan` (synchronous `q_i(λ)` interpolation) + `orca_constraint_block()`; ORCA `%geom` is **0-based** |
| ORCA `.hess` reader | `hess_file.py` | `parse_orca_hess_file()` → `HessFileData` (masses/geometry Bohr/full Cartesian Hessian Eh·Bohr⁻²/frequencies); feeds tsmode |
| CREST conformer search | `crest.py` | `run_conformer_search()` single-stage |
| CREST batch optimization | `crest.py` | `run_batch_optimization()` (-mdopt); retained for upstream compatibility |
| xTB pre-optimization | `xtb.py` | `XTBInterface` standalone (split from crest.py in Phase C); `optimize`/`constrained_optimize`/`relaxed_scan`/`single_point`/`enso_thermo` |
| xTB path search | `xtb_path.py` | `XTBPathInterface.path_search()`, `PathSearchResult`, `path_search()` convenience wrapper |
| xTB relaxed scan | `xtb_scan.py` | `xcontrol_constraint_block()` (1-based xTB `$constrain`), `plan_from_dict()`, `RelaxedScanPoint`/`RelaxedScanResult` |
| xTB thermo | `xtb_thermo.py` | `run_xtb_enso()`, `XTBThermoResult`, `_xyz_to_coord()` |
| CENSO refinement | `censo.py` | `CensoInterface.refine_ensemble()/search()` — presets, rcfile, part templates, JSON/XYZ parsing; env pins `OMP/MKL/OPENBLAS_NUM_THREADS` to `omp-min` (not nproc — CENSO Dask children inherit the env) |
| ISOSTAT clustering | `isostat.py` | `IsostatInterface.cluster()` — Molclus bare-energy title normalisation (exit-24 fix), error classification (timeout/CalledProcess/OSError), env pinning to nthreads |
| Molclus conformer search | `molclus.py` | `MolclusInterface.run_md()` (production, `trajectory_file`/`n_frames` metadata) + `search()` (dormant full pipeline); md.inp byte-for-byte contract, `_MIN_TRAJECTORY_FRAMES` validation |

## CONVENTIONS
- `QCResult` dataclass used universally as return type across all interfaces
- `__init__.py` re-exports 13 stable names from the wrapper modules (`QCInterfaceBase`, `QCResult`, `ORCAInterface`, `CRESTInterface`, `XTBInterface`, `XTBPathInterface`, `IsostatInterface`, `MolclusInterface`, `CensoInterface`, `PathSearchResult`, `path_search`, `run_xtb_enso`, `XTBThermoResult`); the pure helper modules (`constraints`/`hess_file`/`orca_ts`/`route_render`/`xtb_scan`) are imported by full path and are NOT re-exported at package top level
- `XTBInterface` importable from both `cccp.qc.interfaces.xtb` and the `cccp.qc.interfaces` package top-level
- **Thread env pinning**: every subprocess pins `OMP/MKL/OPENBLAS_NUM_THREADS` (LSF/OpenLava injects node-wide `OMP_NUM_THREADS`; the 2026-08-02 curcusone-test oversubscription fix). `xtb`/`isostat`/`molclus` use a `_thread_env()` helper (`xtb_path` reuses `xtb._thread_env`); `crest`/`xtb_thermo`/`censo` build an inline env copy (censo pins to `omp-min`); orca relies on `%pal nprocs` (partial — see audit §3.3)
- **Coordinate indexing**: ORCA constraint blocks are 0-based (`orca_constraint_block` never adds +1); xTB xcontrol blocks are 1-based (`xcontrol_constraint_block` converts). Mixing them silently constrains the wrong atoms.
- **Pure helpers stay pure**: `constraints`, `hess_file`, `orca_ts`, `route_render`, `xtb_scan` must not import `subprocess` or resolve executables — execution belongs to `orca.py`/`xtb*.py`

## NOTES
- **`XTBInterface` split to `xtb.py`** (Phase C, 2026-07-27): was co-located in `crest.py`; now standalone. Top-level re-export preserved for existing consumers.
- **`CRESTInterface` has no base class**: `class CRESTInterface:` — does NOT inherit `QCInterfaceBase` (upstream form restored by 2026-07-13 reverse-sync; kept intentionally). Same for `XTBInterface`/`XTBPathInterface`/`CensoInterface`/`IsostatInterface`/`MolclusInterface`.
- **CENSO/ISOSTAT/Molclus moved into cccp** (2026-08-02): previously lived as subprocess logic inside `acp/backends/censo_backend.py`, `isostat_backend.py`, `molclus_backend.py`; those are now thin adapters delegating here. run_isostat was removed in wave-8. IsostatInterface is the single ISOSTAT path.
- **`run_two_stage_search` removed** (Phase C): was deprecated; The engine was removed in wave-8. Zero consumers remained.
- **Helper modules added for reaction-path / TS / NMR work**: `constraints.py` (coordinate plans), `hess_file.py` (.hess → HessFileData), `orca_ts.py` (OptTS/IRC builders+parsers), `route_render.py` (single route renderer), `xtb_scan.py` (xcontrol + relaxed-scan containers). `acp`/`cccp.calculation` consume them directly; subprocess ownership stays in the 8 wrapper modules.
