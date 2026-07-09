# acp/chem/ — Chemistry Helpers (RDKit)

## OVERVIEW
RDKit-based molecular embedding and XYZ utilities. Thin, stateless functions for SMILES → 3D structure generation used by workflow pipelines. 2 files, ~404 lines.

## STRUCTURE
```
chem/
├── __init__.py       # Re-exports 7 embedding functions (23 lines)
└── embedding.py      # RDKit embedding: SMILES/molfile→XYZ, XYZ parsing, element counting (381 lines)
```

## WHERE TO LOOK
| Task | Function | Notes |
|------|----------|-------|
| SMILES → XYZ | `smiles_to_xyz()` | ETKDG embed + MMFF/UFF opt, line 32 |
| Molfile → XYZ | `molfile_to_xyz()` | Same pipeline for SDF/molfile input, line 105 |
| Multi-frame XYZ | `xyz_to_multiframe_demo()` | Random perturbation for UI preview only, line 179 |
| Element counting | `count_elements_from_xyz()` | First frame only, line 246 |
| Hill formula | `xyz_formula()` | C then H then alphabetically, line 280 |
| XYZ atom records | `parse_xyz_first_frame()` | Dict list with elem/x/y/z, line 310 |
| Frame splitting | `split_xyz_frames()` | Multi-frame → list of single frames, line 348 |

## CONVENTIONS
- **PEP 604 types**: `X | None` with `from __future__ import annotations`
- **Stateless**: No class state, no caching, no fallback on bad input
- **Raise don't hide**: Invalid SMILES/molfile/XYZ → `ValueError` with message
- **RDKit lazy import**: `_require_rdkit()` returns Chem/AllChem on demand
- **ETKDG fallback**: ETKDGv3 → ETKDG → random coords on failure
- **MMFF → UFF**: Primary MMFF forcefield opt, fallback to UFF if MMFF unavailable

## ANTI-PATTERNS
- **Bare `except Exception:`**: 4 instances (lines 88, 92, 163, 167) silently swallow MMFF/UFF optimization failures — optimization can silently degrade
- **Demo-only function**: `xyz_to_multiframe_demo()` is purely cosmetic for UI testing, not scientific
- **No conformer enumeration**: Despite being `chem/`, lacks tautomer/stereoisomer enumeration — workflows rely on CREST for that
- **RDKit coupling**: Every function hard-depends on RDKit — no fallback path for RDKit-less installs
