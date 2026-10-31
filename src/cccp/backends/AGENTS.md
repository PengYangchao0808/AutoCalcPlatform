# cccp/backends/ — QC Backend Capability Layer (implementation home)

## OVERVIEW
Protocol-based quantum chemistry backend layer (moved verbatim from `acp.backends`, plan todo 12). 13 `.py`. All QC execution (subprocess wrappers for external binaries) lives in `cccp.qc.interfaces`; the backends are thin capability-Protocol adapters that pass config through and normalize results via `to_qc_result`. Governance principle: **backends never contain subprocess calls, binary paths, or CLI argument construction.** QCResult's single definition lives in `cccp/qc/interfaces/base.py` and is re-exported here.

## STRUCTURE
```
backends/
├── __init__.py          # Re-exports public symbols (eager backend registration)
├── matrix.py            # 310 L dependency-free single authority: capability DECLARATIONS + status vocabulary
├── base.py              # QCBackend(ABC) + 12 capability Protocols; QCResult/to_qc_result re-export
├── capabilities.py      # Registry-aware introspection: supports/list_capabilities/list_backends/backend_status
├── registry.py          # BackendRegistry, register_backend, get_backend, require_backend
├── orca.py              # ORCABackend — opt/SP/freq/CASSCF/NMR/TS/IRC/relaxed scan + EnGrad
├── crest.py             # CrestBackend — conformer search dispatch (opt/SP/freq raise NotImplementedError)
├── xtb.py               # XTBBackend — opt/constrained opt/SP/relaxed scan/enso thermo/path search
├── censo_backend.py     # CensoBackend(QCBackend, ConformerSearcher) → cccp.qc.interfaces.censo.CensoInterface
├── isostat_backend.py   # IsostatBackend → cccp.qc.interfaces.isostat.IsostatInterface
├── molclus_backend.py   # MolclusBackend → cccp.qc.interfaces.molclus.MolclusInterface
├── external.py          # 5 L shim: batch_process_thermo re-export (cccp.qc.runners)
└── external_backend.py  # ExternalBackend — ISOSTAT clustering + Shermo thermochemistry (per-capability probes)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| ABC base + Protocols | `base.py` | `QCBackend(ABC)` + 12 `@runtime_checkable` Protocols: GeometryOptimizer, ConstrainedOptimizer, SinglePointCalculator, FrequencyCalculator, ConformerSearcher, ClusteringTool, ThermoCalculator, MrrhoThermoCalculator, TSMechanismCalculator, CASSCFCalculator, NmrShieldingCalculator, RelaxedScanCalculator (PEP 544) |
| QCResult | `cccp/qc/interfaces/base.py` | Single definition + `to_qc_result()`; re-exported by `base.py` |
| Capability declarations | `matrix.py` | `CAPABILITY_MATRIX` (7 backends × 20 capability keys), `CAPABILITY_ALIASES`, `TASK_CAPABILITY_MAP` (15 task kinds), `CAPABILITY_BACKEND_PRIORITY`, `BackendCapabilityStatus` (AVAILABLE/STUBBED/NOT_IMPLEMENTED/MISSING_BINARY), `normalize_capability_name`; imports stdlib only |
| Capability introspection | `capabilities.py` | `supports()`, `list_capabilities()`, `list_backends()`, `backend_status()` — declared statuses plus runtime probes (`external` uses `is_isostat_available`/`is_shermo_available`, others `is_available()`) |
| Registry | `registry.py` | `get_backend(name)`, `require_backend(capability)` — declaration-driven selection skips STUBBED/NOT_IMPLEMENTED and raises `UnsupportedCapabilityError`; generic `Registry` in `cccp/core/registry.py` |
| ORCA/CREST/xTB | `orca.py`/`crest.py`/`xtb.py` | Delegate to `cccp.qc.interfaces.*`; ORCA adds `single_point_gradient()` (EnGrad, energy-bound `.engrad` parse) |
| CENSO/ISOSTAT/Molclus | `*_backend.py` | Thin adapters; rcfile/parsing/env-pinning live in cccp interfaces |
| External tools | `external_backend.py` | `ExternalBackend` — ISOSTAT `cluster()` + Shermo `thermochemistry()`/`batch_thermochemistry()` (shared `execute_shermo` + `thermo_normalize`, todo 14); NO direct `run_shermo`, NO task-layer imports |
| Legacy batch thermo shim | `external.py` | 5-line `batch_process_thermo` re-export from `cccp.qc.runners` |
| Legacy batch SP | `acp/backends/batch.py` | NOT here — quarantined legacy backend-direct surface in ACP (see `legacy_batch_quarantine` gate) |

## CONVENTIONS
- **Capability Protocols**: structural subtyping (PEP 544 `@runtime_checkable`); declaration in `matrix.py` is the selection authority — `registry.require_backend()` and `capabilities.*` both read it, and `matrix` never imports them (no cycle)
- **No direct subprocess**: delegate to `cccp.qc.interfaces.*`; no `import subprocess`, no binary paths, no CLI construction here
- **No task-layer imports**: the calculation task surface is reachable only via the pure-type whitelist of the neutral error/contract modules (`errors`, `contracts`) — enforced by the `backend_imports_task_module` gate
- **`to_qc_result()`**: bridges legacy result objects to the single `QCResult`; same-object passthrough for `QCResult` instances
- **Env pinning**: every QC subprocess pins `OMP/MKL/OPENBLAS_NUM_THREADS` to its allocated core count inside cccp interfaces

## ANTI-PATTERNS
- **Do not regress**: historical subprocess logic in `*_backend.py` was consolidated into cccp on 2026-08-02 — never reintroduce it
- **Do not add batch SP here**: the generic batch executor is `cccp/calculation/batch.py` (injected single-task execution functions, no backend capability calls)
- **Do not define QCResult/to_qc_result again**: single definition in `cccp/qc/interfaces/base.py`
- **Do not declare capabilities outside `matrix.py`**: `CAPABILITY_MATRIX`/`CAPABILITY_ALIASES`/`TASK_CAPABILITY_MAP`/`CAPABILITY_BACKEND_PRIORITY` are the single declaration authority (dependency-free, imported by both registry and capabilities)
- **Some backend methods are stubs**: matrix marks them `STUBBED`/`NOT_IMPLEMENTED` (declared == implemented); `CrestBackend.optimize/single_point/frequency` exist only to raise `NotImplementedError`
