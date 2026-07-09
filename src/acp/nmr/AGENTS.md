# acp/nmr/ — NMR Computation Module

## OVERVIEW
Phase 3 NMR computation: shielding tensor parsing, Boltzmann averaging, reference calibration, and report serialization. 4 source files, ~511 lines. Independent of conformer search workflow.

## STRUCTURE
```
nmr/
├── __init__.py      # Re-exports 12 symbols
├── models.py        # 5 dataclasses: NMRAtomShielding, NMRAtomShift, NMRConformerResult, NMRAveragedAtomResult, NMRReport
├── calibration.py   # Boltzmann weighting, reference calibration, conformer selection, atom result averaging
└── parser.py        # Gaussian NMR log parser (GIAO shielding tensor extraction)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Domain models | `models.py` | 5 dataclasses; frozen for immutables (shielding/shift/averaged), mutable for containers (conformer/report) |
| Gaussian NMR parsing | `parser.py` | `parse_gaussian_nmr_log()` — regex-based tensor extraction; `parse_nmr_output()` — backend dispatch |
| Boltzmann averaging | `calibration.py` | `average_atom_results()` — free-energy weighted; `_boltzmann_weights()` — normalized |
| Reference calibration | `calibration.py` | `calibrate_shifts()` — reference→shift conversion |
| Conformer selection | `calibration.py` | `select_conformers()` — window+limit via StructureEnsemble |
| NMR report writing | `reports/nmr_report.py` | `write_json_report()` / `write_xlsx_report()` (lives in acp/reports/) |

## CONVENTIONS
- **Same typing style as acp/**: PEP 604 (`X | None`) with `from __future__ import annotations`
- **`@dataclass(frozen=True)`**: Shielding, Shift, AveragedResult are frozen; ConformerResult and Report are mutable
- **`_normalize_symbol()`**: Defined in 3 files (models, calibration, parser) — local duplication accepted for independence
- **`pyright: reportMissingTypeStubs=false`**: NMR module has pyright suppression comments (locally used tool)
- **Nucleus mapping**: `_NUCLEUS_BY_SYMBOL` dict in `calibration.py` — maps element to default NMR-active nucleus

## ANTI-PATTERNS
- **`_normalize_symbol()` triplicated**: Same logic in models.py (line 12), calibration.py (line 34), parser.py (line 35) — refactor candidate
- **Gaussian-only parser**: `parse_nmr_output()` dispatch only handles Gaussian; ORCA NMR stub not yet implemented
- **HARTREE_TO_KCAL not imported**: Uses `_GAS_CONSTANT_HARTREE` as local constant instead of importing from `conformer_search.utils.constants`
- **pyright comments without pyright configured**: Same pattern as acp/core — locally used tool, not CI-enforced
- **NMR report serialization in separate package**: `write_json_report` / `write_xlsx_report` live in `acp/reports/`, not `acp/nmr/` — cross-package dependency
- **Phase 3 in Phase 1 codebase**: NMR module was built before Phase 3 was officially started — may need integration with WorkflowRunner
