# qc/ — Quantum Chemistry Module

## OVERVIEW
Quantum chemistry subprocess wrappers and job infrastructure. 3 subpackages: external binary interfaces (ORCA/CREST/xTB/CENSO/ISOSTAT/Molclus), thermochemistry runners (ISOSTAT legacy wrapper/Shermo), cluster adapters (LSF/Local). 2026-08-02 consolidation: censo/isostat/molclus interfaces moved from `acp.backends` into `interfaces/` — cccp is the single subprocess layer.

## STRUCTURE
```
qc/
├── interfaces/       # ORCA/CREST/xTB/CENSO/ISOSTAT/Molclus wrappers + base.py(QCInterfaceBase/QCResult) + xtb_thermo.py
├── runners/          # run_isostat (DEPRECATED thin wrapper) + Shermo thermo — all implemented in __init__.py
└── cluster/          # LSF + Local adapters + factory — all implemented in __init__.py
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ABC contract | `interfaces/base.py` | `QCInterfaceBase`, `QCResult` dataclass |
| ORCA DFT opt | `interfaces/orca.py` | Direct ORCA invocation |
| ORCA SP energy | `interfaces/orca.py` | Direct subprocess |
| CREST conformer search | `interfaces/crest.py` | `run_conformer_search()` single-stage; `run_two_stage_search` removed in Phase C |
| CREST batch optimization | `interfaces/crest.py` | `run_batch_optimization()` (-mdopt); retained for upstream compatibility |
| xTB preopt | `interfaces/xtb.py` | Standalone since Phase C (split from crest.py) |
| CENSO refinement | `interfaces/censo.py` | `CensoInterface` — rcfile gen, presets, template injection, JSON/XYZ parsing, env pinning (single subprocess layer; 2026-08-02) |
| ISOSTAT clustering | `interfaces/isostat.py` | `IsostatInterface` — exit-24 title normalisation, error classification, env pinning (single ISOSTAT path; 2026-08-02) |
| Molclus pipeline | `interfaces/molclus.py` | `MolclusInterface.run_md()/search()` — md.inp/settings.ini, trajectory validation, env pinning (2026-08-02) |
| Legacy ISOSTAT | `runners/__init__.py` | DELETED — run_isostat removed in wave-8 |
| Shermo thermo | `runners/__init__.py` | `run_shermo()`, `batch_process_thermo()` (DEPRECATED — prefer acp.backends) |
| LSF job submission | `cluster/__init__.py` | `LSFClusterAdapter` (placeholder) |
| Local execution | `cluster/__init__.py` | `LocalClusterAdapter` |
| Cluster factory | `cluster/__init__.py` | `create_cluster_adapter()` |

## CONVENTIONS
- `QCResult` dataclass is universal return type across all interface files
- `ORCAInterface` inherits from `QCInterfaceBase` ABC; `CRESTInterface`, `XTBInterface`, `CensoInterface`, `IsostatInterface`, `MolclusInterface` do NOT (upstream form, intentional)
- **Thread env pinning**: every QC subprocess pins `OMP/MKL/OPENBLAS_NUM_THREADS` to its allocated core count (LSF injects node-wide `OMP_NUM_THREADS`); censo interfaces pin in a `_thread_env()` helper, orca relies on `%pal nprocs` (see audit §3.3)
- **Subprocess ownership**: `acp/backends` must not contain subprocess logic — cccp interfaces are the single layer (governance principle, 2026-08-02)

## ANTI-PATTERNS (HISTORICAL)
- ~~XTBInterface co-located in `crest.py`~~ **Fixed in Phase C (2026-07-27) — now standalone `xtb.py`**
- ~~censo/isostat/molclus subprocess logic in `acp.backends`~~ **Fixed 2026-08-02 — consolidated into `interfaces/censo.py`, `interfaces/isostat.py`, `interfaces/molclus.py`**
- **`CRESTInterface` has no base class** — `class CRESTInterface:` by design (2026-07-13 reverse-sync restored upstream form). Earlier docs claiming it inherits `QCInterfaceBase` were wrong.

## REMAINING
- **SLURM/PBS adapters unimplemented** — only LSF and Local exist
- **LSF adapter is placeholder** — marked as stub in `cluster/__init__.py`
