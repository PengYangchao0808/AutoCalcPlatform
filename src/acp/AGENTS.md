# acp/ — ACP Unified Module

## OVERVIEW
Phase 1 Python module providing the unified `acp` CLI, stage-based workflow pipeline, capability-driven QC backends, and generic core models. 34 files, ~6.2k lines (incl. NMR/Reports/API/scheduler). Coexists with legacy `conformer_search/` package.

## STRUCTURE
```
acp/
├── cli.py              # `acp run conformer|nmr|serve` subcommand dispatch (~998 lines)
├── __init__.py          # Package docstring only (5 lines)
├── __main__.py          # `python -m acp` works (4 lines)
├── catalog.py           # Protocol catalog listing
├── core/                # Generic mechanism: Structure, WorkflowRunner, Registry, State
├── backends/            # Protocol-based QC adapters (Gaussian/ORCA/CREST/xTB/Isostat/Molclus/external)
├── chem/                # Chemistry-specific logic (RDKit embedding, enumeration)
├── intake/              # Data ingestion: models, parsers, storage
├── io/                  # StructureReader, StructureWriter (thin conformer_search wrapper)
├── workflows/           # Stage-based conformer search + NMR + benchmark + mechanism
├── nmr/                 # Phase 3 NMR: shielding parsing, Boltzmann averaging, calibration
├── reports/             # NMR report serialization (JSON/XLSX)
├── api/                 # Phase 2 FastAPI — server.py/routes.py/v1_routes.py/v1_schemas.py (REAL, ~1600 lines)
└── scheduler/           # Phase 2 task scheduler — jobs, manager, runner, store, provenance (13 files, ~2700 lines)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ACP CLI entry | `cli.py` | `acp run conformer|nmr|mechanism|serve` subcommand dispatch (~998 lines) |
| Config resolution | `cli.py` | `_build_config()` → delegates to `conformer_search.config.load_config` |
| Batch handling | `cli.py` | `_handle_conformer_batch()` JSON summary output |
| Protocol catalog | `catalog.py` | Protocol listing/info commands |
| Core data models | `core/models.py` | Structure, StructureRecord, StructureEnsemble, JobSpec |
| Core workflow engine | `core/workflow.py` | WorkflowSpec, WorkflowRunner, Stage, WorkflowResult |
| State persistence | `core/state.py` | WorkflowState, EventLog (JSONL) |
| Registry pattern | `core/registry.py` | Generic Registry type for extensibility |
| Config facade | `core/config.py` | ACP-side config loading (thin wrapper) |
| Backend ABC | `backends/base.py` | QCBackend(ABC) + 5+ capability Protocols |
| Backend registry | `backends/registry.py` | register_backend, get_backend, require_backend |
| Gaussian backend | `backends/gaussian.py` | Delegates to conformer_search GaussianInterface |
| ORCA backend | `backends/orca.py` | Delegates to conformer_search ORCAInterface |
| CREST backend | `backends/crest.py` | Delegates to conformer_search CRESTInterface |
| xTB backend | `backends/xtb.py` | Delegates to conformer_search XTBInterface |
| Isostat backend | `backends/isostat_backend.py` | Clustering via ISOSTAT |
| Molclus backend | `backends/molclus_backend.py` | Clustering via Molclus |
| External tools | `backends/external.py` | run_isostat, run_shermo, batch_process_thermo |
| External backend | `backends/external_backend.py` | External tool adapter |
| IO wrapper | `io/structures.py` | StructureReader.detect_format/read, StructureWriter |
| RDKit embedding | `chem/embedding.py` | SMILES→3D, charge assignment, enumeration (404 lines) |
| Intake models | `intake/models.py` | Data ingestion domain models |
| Intake parsers | `intake/parsers.py` | File parsing logic |
| Intake storage | `intake/storage.py` | Result storage |
| Conformer workflow | `workflows/conformer.py` | 7 stage functions + run_conformer_search() |
| NMR workflow | `workflows/nmr.py` | run_nmr_calculation, stage_nmr_build_report (ORCA NMR = NotImplementedError) |
| Benchmark workflow | `workflows/benchmark.py` | BenchmarkRunner, run_benchmark |
| Mechanism workflow | `workflows/mechanism.py` | TS search + IRC validation (now implemented) |
| NMR models | `nmr/models.py` | 5 NMR dataclasses (shielding/shift/conformer/averaged/report) |
| NMR calibration | `nmr/calibration.py` | Boltzmann averaging, reference calibration |
| NMR log parsing | `nmr/parser.py` | Gaussian GIAO shielding tensor extraction |
| NMR reports | `reports/nmr_report.py` | JSON/XLSX serialization |
| API server | `api/server.py` | FastAPI app factory + static frontend hosting; `/api/status`,`/api/backends` live |
| API routes | `api/routes.py` | `/api/status`,`/api/backends` implemented; `/api/jobs` + SSE NOT yet |
| API v1 routes | `api/v1_routes.py` | Job submission, molecule upload, task mgmt (~700 lines) |
| API schemas | `api/schemas.py` / v1_schemas.py | Pydantic models for status, backends, jobs |
| Scheduler jobs | `scheduler/jobs.py` | Job data models (Job, JobStatus, JobConfig) |
| Job manager | `scheduler/manager.py` | Lifecycle management, polling, cancellation |
| Job runner | `scheduler/runner.py` | Background process execution (593 lines) |
| Task store | `scheduler/store.py` | Persistent job storage |
| Provenance | `scheduler/provenance.py` | Event sourcing, audit logging |
| Artifacts | `scheduler/artifacts.py` | Job artifact management |
| Stage tasks | `scheduler/stage_tasks.py` | Workflow stage task wrappers (403 lines) |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` throughout
- **Docstrings**: Compact (single-line or short block), unlike verbose Google-style in `conformer_search/`
- **Backend design**: Capability Protocols instead of monolithic ABC — backend declares what it can do
- **No chem logic in core/**: core/ contains only generic mechanism (Structure, WorkflowRunner, Registry)
- **Stage pipeline**: WorkflowSpec assembles Stage functions; WorkflowRunner executes sequentially
- **Backward compat**: acp delegates to conformer_search for actual QC execution and config loading
- **`__all__`**: Every `__init__.py` re-exports public symbols

## ANTI-PATTERNS
- **Thin wrapper syndrome**: acp/io and acp/core/config largely delegate to conformer_search — adds abstraction with minimal independent logic
- **Depends on legacy package**: acp imports from `conformer_search.config`, `conformer_search.io`, `conformer_search.core.engine` — creates coupling
- **`__main__.py` exists**: `python -m acp` works (old docs claimed it crashes)
- **scheduler/ and api/ are REAL now**: Both are substantial modules (~2700 lines scheduler, ~1600 lines api). Missing: `api` optional deps in pyproject.toml, `/api/jobs` + SSE endpoints, scheduler integration with API
- **`# pyright:` suppressions widespread**: 14 acp/ files suppress type-checking rules (up to 10 each). pyright not in project toolchain
- **bare `except Exception:`**: 8 instances across api/, chem/, cli.py, scheduler/ silently swallow errors
- **No CI tests specific to acp package boundaries**: tests live in shared tests/ directory
- **HARTREE_TO_KCAL duplication**: Defined in both `acp/core/models.py` and `conformer_search/utils/constants.py`