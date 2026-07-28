# qc/interfaces/ — QC Software Interfaces

## OVERVIEW
Subprocess wrappers for external quantum chemistry binaries: ORCA, CREST, xTB. Everything shells out to external binaries.

## STRUCTURE
```
interfaces/
├── __init__.py    # Re-exports 7 symbols
├── base.py        # QCInterfaceBase(ABC) + QCResult dataclass
├── orca.py        # ORCA opt/SP/freq interface
├── crest.py       # CRESTInterface — conformer search + batch -mdopt
├── xtb.py         # XTBInterface — optimize / single_point / enso_thermo
└── xtb_thermo.py  # xTB --bhess --enso thermo runner + XTBThermoResult
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC contract | `base.py` | `QCInterfaceBase`, `QCResult` dataclass |
| ORCA SP energy | `orca.py` | Direct subprocess invocation |
| CREST conformer search | `crest.py` | `run_conformer_search()` single-stage |
| CREST batch optimization | `crest.py` | `run_batch_optimization()` (-mdopt); used by dormant `ConformerEngine` |
| xTB pre-optimization | `xtb.py` | `XTBInterface` standalone (split from crest.py in Phase C) |
| xTB thermo | `xtb_thermo.py` | `run_xtb_enso()`, `_xyz_to_coord()` |

## CONVENTIONS
- `QCResult` dataclass used universally as return type across all interfaces
- All symbols re-exported via `__init__.py`
- `XTBInterface` importable from both `cccp.qc.interfaces.xtb` and the `cccp.qc.interfaces` package top-level

## NOTES
- **`XTBInterface` split to `xtb.py`** (Phase C, 2026-07-27): was co-located in `crest.py`; now standalone. Top-level re-export preserved for existing consumers.
- **`CRESTInterface` has no base class**: `class CRESTInterface:` — does NOT inherit `QCInterfaceBase` (upstream form restored by 2026-07-13 reverse-sync; kept intentionally).
- **`run_two_stage_search` removed** (Phase C): was deprecated; `ConformerEngine` uses `_step_two_stage_crest()` internally and never called it. Zero consumers remained.
