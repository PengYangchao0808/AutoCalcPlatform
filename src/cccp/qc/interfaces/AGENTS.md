# qc/interfaces/ — QC Software Interfaces

## OVERVIEW
Subprocess wrappers for external quantum chemistry binaries: ORCA, CREST, xTB, CENSO, ISOSTAT, Molclus. Everything shells out to external binaries — this package is the **single subprocess layer** (governance principle since the 2026-08-02 consolidation: `acp/backends` must not own subprocess logic).

## STRUCTURE
```
interfaces/
├── __init__.py    # Re-exports 10 symbols
├── base.py        # QCInterfaceBase(ABC) + QCResult dataclass
├── orca.py        # ORCA opt/SP/freq interface
├── crest.py       # CRESTInterface — conformer search + batch -mdopt
├── xtb.py         # XTBInterface — optimize / single_point / enso_thermo
├── xtb_thermo.py  # xTB --bhess --enso thermo runner + XTBThermoResult
├── censo.py       # CensoInterface — rcfile gen, presets, template injection, JSON/XYZ parsing (2026-08-02)
├── isostat.py     # IsostatInterface — cluster() with exit-24 title normalisation (2026-08-02)
└── molclus.py     # MolclusInterface — run_md()/search() xTB-MD pipeline (2026-08-02)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC contract | `base.py` | `QCInterfaceBase`, `QCResult` dataclass |
| ORCA SP energy | `orca.py` | Direct subprocess invocation |
| CREST conformer search | `crest.py` | `run_conformer_search()` single-stage |
| CREST batch optimization | `crest.py` | `run_batch_optimization()` (-mdopt); retained for upstream compatibility |
| xTB pre-optimization | `xtb.py` | `XTBInterface` standalone (split from crest.py in Phase C) |
| xTB thermo | `xtb_thermo.py` | `run_xtb_enso()`, `_xyz_to_coord()` |
| CENSO refinement | `censo.py` | `CensoInterface.refine_ensemble()/search()` — presets, rcfile, part templates, JSON/XYZ parsing; env pins `OMP/MKL/OPENBLAS_NUM_THREADS` to nproc |
| ISOSTAT clustering | `isostat.py` | `IsostatInterface.cluster()` — Molclus bare-energy title normalisation (exit-24 fix), error classification (timeout/CalledProcess/OSError), env pinning to nthreads |
| Molclus conformer search | `molclus.py` | `MolclusInterface.run_md()` (production, `trajectory_file`/`n_frames` metadata) + `search()` (dormant full pipeline); md.inp byte-for-byte contract, `_MIN_TRAJECTORY_FRAMES` validation |

## CONVENTIONS
- `QCResult` dataclass used universally as return type across all interfaces
- All symbols re-exported via `__init__.py`
- `XTBInterface` importable from both `cccp.qc.interfaces.xtb` and the `cccp.qc.interfaces` package top-level
- **Thread env pinning**: every subprocess pins `OMP/MKL/OPENBLAS_NUM_THREADS` (LSF/OpenLava injects node-wide `OMP_NUM_THREADS`; the 2026-08-02 curcusone-test oversubscription fix). xtb/crest/xtb_thermo/censo/isostat/molclus pin; orca relies on `%pal nprocs` (partial — see audit §3.3)

## NOTES
- **`XTBInterface` split to `xtb.py`** (Phase C, 2026-07-27): was co-located in `crest.py`; now standalone. Top-level re-export preserved for existing consumers.
- **`CRESTInterface` has no base class**: `class CRESTInterface:` — does NOT inherit `QCInterfaceBase` (upstream form restored by 2026-07-13 reverse-sync; kept intentionally).
- **CENSO/ISOSTAT/Molclus moved into cccp** (2026-08-02): previously lived as subprocess logic inside `acp/backends/censo_backend.py`, `isostat_backend.py`, `molclus_backend.py`; those are now thin adapters delegating here. run_isostat was removed in wave-8. IsostatInterface is the single ISOSTAT path.
- **`run_two_stage_search` removed** (Phase C): was deprecated; The engine was removed in wave-8. Zero consumers remained.
