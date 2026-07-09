# acp/backends/ — QC Backend Adapters

## OVERVIEW
Protocol-based quantum chemistry backend layer. Replaces the legacy `conformer_search/qc/interfaces/` and `qc/runners/` with a capability-driven design. 8 files, ~776 lines.

## STRUCTURE
```
backends/
├── __init__.py          # Re-exports 18 symbols (QCBackend, Protocols, backends, registry, external)
├── base.py              # QCBackend(ABC), QCResult dataclass, 8 capability Protocols (186 lines)
├── capabilities.py      # Additional capability Protocols
├── registry.py          # BackendRegistry, register_backend, get_backend, require_backend
├── gaussian.py          # GaussianBackend (GeometryOptimizer + SinglePointCalculator)
├── orca.py              # ORCABackend (SinglePointCalculator + GeometryOptimizer)
├── crest.py             # CrestBackend (conformer search dispatch)
├── xtb.py               # XTBBackend (pre-optimization)
├── external.py          # run_isostat, run_shermo, batch_process_thermo
├── external_backend.py  # External tool backend adapter
├── isostat_backend.py   # Isostat clustering backend
└── molclus_backend.py   # Molclus clustering backend
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC base | `base.py` | QCBackend(ABC) — is_available(), get_version() |
| Protocols | `base.py` + `capabilities.py` | 8 Protocols: GeometryOptimizer, SinglePointCalculator, FrequencyCalculator, NMRCalculator, TSMechanismCalculator, ConformerSearcher, ClusteringTool, ThermoCalculator |
| QCResult | `base.py` | Standard result dataclass, to_qc_result() normalization function |
| Gaussian | `gaussian.py` | Delegates to `conformer_search.qc.interfaces.gaussian.GaussianInterface` |
| ORCA | `orca.py` | Delegates to `conformer_search.qc.interfaces.orca.ORCAInterface` |
| CREST | `crest.py` | Delegates to `conformer_search.qc.interfaces.crest.CRESTInterface` |
| xTB | `xtb.py` | Delegates to `conformer_search.qc.interfaces.xtb.XTBInterface` |
| External tools | `external.py` | run_isostat, run_shermo, batch_process_thermo |
| External backend | `external_backend.py` | External tool adapter |
| Isostat backend | `isostat_backend.py` | ISOSTAT clustering wrapper |
| Molclus backend | `molclus_backend.py` | Molclus clustering wrapper |
| Registry | `registry.py` | Registration + discovery of available backends |

## CONVENTIONS
- **Capability Protocols**: Backends declare what they can do via structural subtyping (PEP 544 `@runtime_checkable`)
- **No direct subprocess**: All backends delegate to `conformer_search.qc.interfaces.*` for actual execution
- **`QCResult` normalization**: `to_qc_result()` bridges legacy result objects to standardized `QCResult` dataclass
- **`@runtime_checkable`**: Protocol classes marked `@runtime_checkable` for `isinstance()` checks

## ANTI-PATTERNS
- **Thin delegation layer**: Most backends simply wrap conformer_search interfaces — adds abstraction with minimal new logic
- **No standalone testing**: Backend tests depend on legacy interface mocks
- **No cluster-awareness**: Backends don't handle LSF/SLURM/PBS submission (delegated to conformer_search cluster layer)
- **Some backend methods are stubs**: Not all capability Protocols are fully implemented on every backend