# acp/backends/ — QC Backend Adapters

## OVERVIEW
Protocol-based quantum chemistry backend layer. All QC execution (subprocess wrappers for external binaries) lives in `cccp.qc.interfaces`; the backends are thin capability-Protocol adapters that pass config through and normalize results via `to_qc_result`. Governance principle: **acp/backends never contains subprocess calls, binary paths, or CLI argument construction.** 13 files, ~1525 lines.

## STRUCTURE
```
backends/
├── __init__.py          # Re-exports public symbols (QCBackend, Protocols, backends, registry, external)
├── base.py              # QCBackend(ABC), QCResult dataclass, capability Protocols (186 lines)
├── capabilities.py      # Additional capability Protocols
├── registry.py          # BackendRegistry, register_backend, get_backend, require_backend
├── gaussian.py          # GaussianBackend (GeometryOptimizer + SinglePointCalculator)
├── orca.py              # ORCABackend (SinglePointCalculator + GeometryOptimizer)
├── crest.py             # CrestBackend (conformer search dispatch)
├── xtb.py               # XTBBackend (pre-optimization)
├── censo_backend.py     # CENSO adapter → cccp.qc.interfaces.censo.CensoInterface
├── isostat_backend.py   # ISOSTAT adapter → cccp.qc.interfaces.isostat.IsostatInterface
├── molclus_backend.py   # Molclus adapter → cccp.qc.interfaces.molclus.MolclusInterface
├── external.py          # batch_process_thermo re-exports (run_isostat removed in wave-8)
└── external_backend.py  # External tool backend adapter (shermo; cluster() → IsostatInterface)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC base | `base.py` | QCBackend(ABC) — is_available(), get_version() |
| Protocols | `base.py` + `capabilities.py` | Protocols: GeometryOptimizer, SinglePointCalculator, FrequencyCalculator, NMRCalculator, TSMechanismCalculator, ConformerSearcher, ClusteringTool, ThermoCalculator |
| QCResult | `base.py` | Standard result dataclass, `to_qc_result()` normalization function |
| Gaussian | `gaussian.py` | Delegates to `cccp.qc.interfaces.gaussian.GaussianInterface` |
| ORCA | `orca.py` | Delegates to `cccp.qc.interfaces.orca.ORCAInterface` |
| CREST | `crest.py` | Delegates to `cccp.qc.interfaces.crest.CRESTInterface` |
| xTB | `xtb.py` | Delegates to `cccp.qc.interfaces.xtb.XTBInterface` |
| CENSO | `censo_backend.py` | Thin adapter → `cccp.qc.interfaces.censo.CensoInterface` (rcfile/preset/parsing all in cccp) |
| ISOSTAT | `isostat_backend.py` | Thin adapter → `cccp.qc.interfaces.isostat.IsostatInterface.cluster()` (title normalisation, env pinning in cccp) |
| Molclus | `molclus_backend.py` | Thin adapter → `cccp.qc.interfaces.molclus.MolclusInterface.run_md()/search()` (md.inp, settings.ini, trajectory validation in cccp) |
| External tools | `external.py` | Re-exports batch_process_thermo (cccp runners; run_isostat removed in wave-8) |
| External backend | `external_backend.py` | External tool adapter; `cluster()` routes through `IsostatInterface` |
| Registry | `registry.py` | Registration + discovery of available backends |

## CONVENTIONS
- **Capability Protocols**: Backends declare what they can do via structural subtyping (PEP 544 `@runtime_checkable`)
- **No direct subprocess**: All backends delegate to `cccp.qc.interfaces.*` for actual execution; no `import subprocess`, no binary paths, no CLI argument construction in this package
- **`QCResult` normalization**: `to_qc_result()` bridges legacy result objects to standardized `QCResult` dataclass
- **`@runtime_checkable`**: Protocol classes marked `@runtime_checkable` for `isinstance()` checks
- **Env pinning**: Every QC subprocess (xtb/orca/crest/censo/isostat/shermo) pins `OMP/MKL/OPENBLAS_NUM_THREADS` to its allocated core count inside cccp interfaces — never rely on the inherited LSF `OMP_NUM_THREADS`

## ANTI-PATTERNS
- **Thin delegation layer**: Most backends simply wrap cccp interfaces — adds abstraction with minimal new logic (accepted: capability Protocols are the point)
- **No standalone testing**: Backend tests depend on interface mocks
- **No cluster-awareness**: Backends don't handle LSF/SLURM/PBS submission (delegated to cccp cluster layer)
- **Some backend methods are stubs**: Not all capability Protocols are fully implemented on every backend
- **Do not regress**: historically `censo_backend.py`/`isostat_backend.py`/`molclus_backend.py` carried their own subprocess logic (~65K lines total); consolidated into cccp on 2026-08-02 — never reintroduce subprocess logic here
