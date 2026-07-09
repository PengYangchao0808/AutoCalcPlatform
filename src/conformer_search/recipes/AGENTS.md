# conformer_search/recipes/ — CENSO Recipe Adapters

## OVERVIEW
CENSO protocol adapter layer: legacy-to-funnel-record conversion (adapter.py, 417 lines) and Part0–Part3 funnel execution stages (censo_parts.py, 189 lines). Maps CENSO protocol names (full/lite/zero) to workflow stage configurations.

## STRUCTURE
```
recipes/
├── __init__.py      # Package init (no __init__.py found on disk)
├── adapter.py       # Bidirectional conversion: ConformerCandidate ⟷ FunnelRecord,
│                    #   CandidateSet ⟷ FunnelRecordSet, StructureEnsemble bridge
│                    #   Metadata persistence via _legacy_candidate_adapter key
└── censo_parts.py   # Part0 (xTB prescreen), Part1 (low-cost DFT SP rerank),
                     #   Part2 (DFT opt + free energy), Part3 (Boltzmann cutoff)
                     #   Snapshot JSON/MD trace writers
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Legacy → FunnelRecord | `adapter.py` line 184 | `funnel_record_from_candidate()` |
| FunnelRecord → Legacy | `adapter.py` line 239 | `candidate_from_funnel_record()` |
| Batch conversion | `adapter.py` lines 292, 319 | Set-level `funnel_records_from_candidate_set`, `candidate_set_from_funnel_records` |
| ACP bridge | `adapter.py` lines 363, 377 | `funnel_records_from_structure_ensemble`, `structure_ensemble_from_funnel_records` |
| xTB prescreen (Part0) | `censo_parts.py` line 93 | Window-based or top-N filtering on `xtb_sp` key |
| Low-cost DFT SP (Part1) | `censo_parts.py` line 117 | Rerank on `low_cost_dft_sp`, window select |
| DFT opt + free E (Part2) | `censo_parts.py` line 138 | Sort on `r2scan3c_sp`, window select |
| Boltzmann refine (Part3) | `censo_parts.py` line 159 | Cutoff on `final_gibbs`, temperature-aware |
| Snapshot/trace output | `censo_parts.py` lines 37, 63 | Per-stage JSON + markdown funnel trace |
| Canonical energy keys | `censo_parts.py` lines 23-27 | `KEY_XTB`, `KEY_LOWCOST`, `KEY_R2SCAN`, `KEY_FINAL_E`, `KEY_FINAL_G` |

## CONVENTIONS
- **Module docstring**: Title + `====` underline + description + `Author: QCcalc Team` (adapter.py)
- **`from __future__ import annotations`**: Both files use PEP 604 style
- **Imports**: `conformer_search.*` full package path; `acp.core.models` for ACP bridge
- **`__all__`**: Both files export public symbols (adapter.py: 7 functions; censo_parts.py: 9 symbols)
- **Snapshot format**: JSON per stage, markdown funnel trace for human review
- **Energy keys**: Canonical string constants defined in censo_parts.py, used across both files

## ANTI-PATTERNS
- **No `__init__.py` present** — `recipes/` has no package init file on disk; may cause import issues
- **`HARTREE_TO_KCAL`** imported from `conformer_search.utils.constants` — also defined at `acp.core.models`; modify both
- **Part stage naming**: Part0–Part3 named inconsistently: Part2 sorts on `KEY_R2SCAN` but stage name says "part2_optimization"
- **adapter.py modules**: Imports `read_xyz` from `conformer_search.utils.file_io` (tight coupling) and `records_from_paths` from `conformer_search.ensemble.candidate_set` (cross-package dependency)
