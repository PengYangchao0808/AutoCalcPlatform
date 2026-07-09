# qc/ — Quantum Chemistry Module

## OVERVIEW
Quantum chemistry subprocess wrappers and job infrastructure. 3 subpackages: external binary interfaces (Gaussian/ORCA/CREST/xTB), thermochemistry runners (ISOSTAT/Shermo), cluster adapters (LSF/Local).

## STRUCTURE
```
qc/
├── interfaces/       # QCInterfaceBase ABC + Gaussian/ORCA/CREST/xTB wrappers
├── runners/          # ISOSTAT (isostat.py) + Shermo thermo (shermo.py); __init__.py only re-exports
└── cluster/          # LSF (lsf.py) + Local (local.py) + base.py; __init__.py only re-exports + factory
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ABC contract | `interfaces/base.py` | `QCInterfaceBase`, `QCResult` dataclass |
| Gaussian DFT opt | `interfaces/gaussian.py` | Subprocess via `scripts/run_g16_worker.sh` |
| ORCA SP energy | `interfaces/orca.py` | Direct subprocess; NMR raises NotImplementedError |
| CREST conformer search | `interfaces/crest.py` | Two-stage GFN0 → GFN2 |
| xTB preopt | `interfaces/xtb.py` | Now separated from crest.py (Phase 1 fix) |
| ISOSTAT clustering | `runners/isostat.py` | `run_isostat()` (DEPRECATED — prefer acp.backends) |
| Shermo thermo | `runners/shermo.py` | `run_shermo()`, `batch_process_thermo()` (DEPRECATED — prefer acp.backends) |
| LSF job submission | `cluster/lsf.py` | `LSFClusterAdapter` (placeholder, 207 lines) |
| Local execution | `cluster/local.py` | `LocalClusterAdapter` (169 lines) |
| Cluster base ABC | `cluster/base.py` | `ClusterAdapterBase` (103 lines) |
| Cluster factory | `cluster/__init__.py` | `create_cluster_adapter()` — only impl in __init__.py |

## CONVENTIONS
- `QCResult` dataclass is universal return type across all interface files
- Gaussian/ORCA interfaces inherit from `QCInterfaceBase` ABC
- `scripts/run_g16_worker.sh` must be used for Gaussian — never bypass

## ANTI-PATTERNS (HISTORICAL — Phase 1 resolved)
- ~~Implementation in `__init__.py`: runners 277→18 lines, cluster 474→51 lines~~ **Fixed**
- ~~CRESTInterface inherited `object`~~ **Fixed — now inherits QCInterfaceBase**
- ~~XTBInterface co-located in `crest.py`~~ **Fixed — now standalone xtb.py**

## REMAINING
- **SLURM/PBS adapters unimplemented** — only LSF and Local exist
- **LSF adapter is placeholder** — `cluster/__init__.py:266-268` marked as stub
