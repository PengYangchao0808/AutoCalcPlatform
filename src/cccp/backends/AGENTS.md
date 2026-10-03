# cccp/backends/ — QC Backend Capability Layer (implementation home)

## OVERVIEW
Protocol-based quantum chemistry backend layer (moved verbatim from `acp.backends`, plan todo 12). All QC execution (subprocess wrappers for external binaries) lives in `cccp.qc.interfaces`; the backends are thin capability-Protocol adapters that pass config through and normalize results via `to_qc_result`. Governance principle: **backends never contain subprocess calls, binary paths, or CLI argument construction.** QCResult's single definition lives in `cccp/qc/interfaces/base.py` and is re-exported here.

## STRUCTURE
```
backends/
├── __init__.py          # Re-exports public symbols (eager backend registration)
├── matrix.py            # Declarative capability matrix (dependency-free, todo 8)
├── base.py              # QCBackend(ABC) + capability Protocols; QCResult/to_qc_result re-export
├── capabilities.py      # supports/list_capabilities/list_backends/backend_status
├── registry.py          # BackendRegistry, register_backend, get_backend, require_backend
├── orca.py              # ORCABackend (SinglePointCalculator + GeometryOptimizer)
├── crest.py             # CrestBackend (conformer search dispatch)
├── xtb.py               # XTBBackend (opt/sp/scan/path/thermo)
├── censo_backend.py     # CENSO adapter → cccp.qc.interfaces.censo.CensoInterface
├── isostat_backend.py   # ISOSTAT adapter → cccp.qc.interfaces.isostat.IsostatInterface
├── molclus_backend.py   # Molclus adapter → cccp.qc.interfaces.molclus.MolclusInterface
├── external.py          # batch_process_thermo re-export (cccp.qc.runners)
└── external_backend.py  # External tool backend (Shermo via cccp.qc.shermo_adapter; cluster() → IsostatInterface)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC base + Protocols | `base.py` | QCBackend(ABC); GeometryOptimizer/SinglePointCalculator/FrequencyCalculator/TSMechanismCalculator/ConformerSearcher/ClusteringTool/ThermoCalculator/... (PEP 544) |
| QCResult | `cccp/qc/interfaces/base.py` | Single definition + `to_qc_result()`; re-exported by `base.py` |
| Capability declarations | `matrix.py` | `CAPABILITY_MATRIX`/`BackendCapabilityStatus`/`normalize_capability_name` (declared == implemented) |
| Registry | `registry.py` | `get_backend(name)`, `require_backend(capability)`; generic `Registry` in `cccp/core/registry.py` |
| ORCA/CREST/xTB | `orca.py`/`crest.py`/`xtb.py` | Delegate to `cccp.qc.interfaces.*` |
| CENSO/ISOSTAT/Molclus | `*_backend.py` | Thin adapters; rcfile/parsing/env-pinning live in cccp interfaces |
| External tools | `external_backend.py` | Thermochemistry via shared `execute_shermo` + `thermo_normalize` (todo 14); NO direct `run_shermo`, NO task-layer imports |
| Legacy batch SP | `acp/backends/batch.py` | NOT here — quarantined legacy backend-direct surface in ACP (see `legacy_batch_quarantine` gate) |

## CONVENTIONS
- **Capability Protocols**: structural subtyping (PEP 544 `@runtime_checkable`); declaration in `matrix.py` is the selection authority
- **No direct subprocess**: delegate to `cccp.qc.interfaces.*`; no `import subprocess`, no binary paths, no CLI construction here
- **No task-layer imports**: the calculation task surface is reachable only via the pure-type whitelist of the neutral error/contract modules (`errors`, `contracts`) — enforced by the `backend_imports_task_module` gate
- **`to_qc_result()`**: bridges legacy result objects to the single `QCResult`; same-object passthrough for `QCResult` instances
- **Env pinning**: every QC subprocess pins `OMP/MKL/OPENBLAS_NUM_THREADS` to its allocated core count inside cccp interfaces

## ANTI-PATTERNS
- **Do not regress**: historical subprocess logic in `*_backend.py` was consolidated into cccp on 2026-08-02 — never reintroduce it
- **Do not add batch SP here**: the generic batch executor is `cccp/calculation/batch.py` (injected single-task execution functions, no backend capability calls)
- **Do not define QCResult/to_qc_result again**: single definition in `cccp/qc/interfaces/base.py`
- **Some backend methods are stubs**: matrix marks them `STUBBED`/`NOT_IMPLEMENTED` (declared == implemented)
