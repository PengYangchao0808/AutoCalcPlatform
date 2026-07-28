# qc/ — Quantum Chemistry Module

## OVERVIEW
Quantum chemistry subprocess wrappers and job infrastructure. 3 subpackages: external binary interfaces (ORCA/CREST/xTB), thermochemistry runners (ISOSTAT/Shermo), cluster adapters (LSF/Local).

## STRUCTURE
```
qc/
├── interfaces/       # ORCA/CREST/xTB wrappers + base.py(QCInterfaceBase/QCResult) + xtb_thermo.py
├── runners/          # ISOSTAT + Shermo thermo — all implemented in __init__.py
└── cluster/          # LSF + Local adapters + factory — all implemented in __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ABC contract | `interfaces/base.py` | `QCInterfaceBase`, `QCResult` dataclass |
| ORCA DFT opt | `interfaces/orca.py` | Direct ORCA invocation |
| ORCA SP energy | `interfaces/orca.py` | Direct subprocess |
| CREST conformer search | `interfaces/crest.py` | `run_conformer_search()` single-stage; `run_two_stage_search` removed in Phase C |
| CREST batch optimization | `interfaces/crest.py` | `run_batch_optimization()` (-mdopt); kept for dormant `ConformerEngine` |
| xTB preopt | `interfaces/xtb.py` | Standalone since Phase C (split from crest.py) |
| ISOSTAT clustering | `runners/__init__.py` | `run_isostat()` (DEPRECATED — prefer acp.backends) |
| Shermo thermo | `runners/__init__.py` | `run_shermo()`, `batch_process_thermo()` (DEPRECATED — prefer acp.backends) |
| LSF job submission | `cluster/__init__.py` | `LSFClusterAdapter` (placeholder) |
| Local execution | `cluster/__init__.py` | `LocalClusterAdapter` |
| Cluster factory | `cluster/__init__.py` | `create_cluster_adapter()` |

## CONVENTIONS
- `QCResult` dataclass is universal return type across all interface files
- `ORCAInterface` inherits from `QCInterfaceBase` ABC; `CRESTInterface` and `XTBInterface` do NOT (upstream form, intentional)

## ANTI-PATTERNS (HISTORICAL)
- ~~XTBInterface co-located in `crest.py`~~ **Fixed in Phase C (2026-07-27) — now standalone `xtb.py`**
- **`CRESTInterface` has no base class** — `class CRESTInterface:` by design (2026-07-13 reverse-sync restored upstream form). Earlier docs claiming it inherits `QCInterfaceBase` were wrong.

## REMAINING
- **SLURM/PBS adapters unimplemented** — only LSF and Local exist
- **LSF adapter is placeholder** — marked as stub in `cluster/__init__.py`
