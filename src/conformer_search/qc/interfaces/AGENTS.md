# qc/interfaces/ — QC Software Interfaces

## OVERVIEW
Subprocess wrappers for external quantum chemistry binaries: Gaussian 16, ORCA, CREST, xTB. ABC base + 4 implementations. Everything shells out to external binaries.

## STRUCTURE
```
interfaces/
├── __init__.py    # Re-exports 6 symbols
├── base.py        # QCInterfaceBase(ABC) + QCResult dataclass (153 lines)
├── gaussian.py    # Gaussian 16 optimizer (443 lines)
├── orca.py        # ORCA single-point energy (418 lines)
└── crest.py       # CREST conformer search + XTBInterface co-located (448 lines)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC contract | `base.py` | `QCInterfaceBase`, `QCResult` dataclass |
| Gaussian DFT opt | `gaussian.py` | Subprocess via `scripts/run_g16_worker.sh` |
| ORCA SP energy | `orca.py` | Direct subprocess invocation |
| CREST conformer search | `crest.py` | Two-stage: GFN0 → GFN2 |
| xTB pre-optimization | `crest.py` | `XTBInterface` co-located, not in own file |

## CONVENTIONS
- `QCResult` dataclass used universally as return type across all interfaces
- All symbols re-exported via `__init__.py`

## NOTES
- **`XTBInterface` moved to `xtb.py`**: Was co-located in `crest.py`, now has its own file (Phase 1 fix).
- **`CRESTInterface` now inherits `QCInterfaceBase`**: Was `object`, fixed in Phase 1.
