# acp/ — ACP Unified Module

## OVERVIEW
The unified `acp` CLI, stage-based workflow pipeline, capability-driven QC backends, and generic core models. ~40 files, ~8k lines (incl. API + scheduler). Coexists with the underlying `cccp` package (Computational Chemistry Connection Package — the QC interface library). Active workflows: ensemble, energy, xtbmd_censo_energy, mechanism, nmr (GIAO + DP4/DP5, reactivated 2026-08-07), simple (singlepoint/opt/freq/optfreq/optfreqsp/scan/xtb-opt). The conformer/benchmark workflows were retired on 2026-07-27 (nmr was retired then but revived in P1a).

## STRUCTURE
```
acp/
├── cli.py              # `acp run {ensemble|energy|mechanism|serve|simple workflows}` (~1835 lines)
├── __init__.py          # Package docstring only
├── __main__.py          # `python -m acp` works
├── catalog.py           # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS (retired entries kept as status:"retired")
├── core/                # Generic mechanism: Structure, WorkflowRunner, Registry, State, Config
├── backends/            # Capability-Protocol QC adapters (ORCA/CREST/xTB/CENSO/Isostat/Molclus/external)
├── chem/                # Chemistry: RDKit embedding, composition analysis
├── intake/              # Data ingestion: models, parsers, storage
├── io/                  # StructureReader / StructureWriter (thin cccp wrapper)
├── workflows/           # ensemble, energy, mechanism, simple + registry (see workflows/AGENTS.md)
├── api/                 # FastAPI — server, routes, v1_routes, schemas (~1600 lines)
└── scheduler/           # Task scheduler — jobs, manager, runner, store, provenance, stage_tasks, ... + remote/ (~24 files)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ACP CLI entry | `cli.py` | `acp run {ensemble\|energy\|xtbmd_censo_energy\|mechanism\|serve\|singlepoint\|opt\|freq\|...}` dispatch (~2000 lines) |
| Config resolution | `cli.py` | `_build_config()` → delegates to `cccp.config.load_config` |
| Workflow catalog | `catalog.py` | `WORKFLOW_CATALOG` (active + retired), `SUPPORTED_WORKFLOWS`, `METHOD_META`, `METHOD_SCHEMAS` |
| Method catalog | `catalog.py` | `METHOD_META` dict: wB97X-D4, r2SCAN-3c, DLPNO-CCSD(T) route blocks |
| Core data models | `core/models.py` | Structure, StructureRecord, StructureEnsemble, JobSpec |
| Core workflow engine | `core/workflow.py` | WorkflowSpec, WorkflowRunner, Stage, WorkflowResult |
| State persistence | `core/state.py` | WorkflowState, EventLog (JSONL) |
| Registry pattern | `core/registry.py` | Generic Registry type for extensibility |
| Config facade | `core/config.py` | ACP-side config loading (thin wrapper around cccp.config) |
| Backend capability Protocols | `backends/base.py` | GeometryOptimizer / SinglePointCalculator / ConformerSearcher / ... (PEP 544 Protocols) |
| Backend registry | `backends/registry.py` | `register_backend`, `get_backend(name)`, `require_backend(capability)` |
| ORCA backend | `backends/orca.py` | Delegates to cccp ORCAInterface |
| CREST backend | `backends/crest.py` | Delegates to cccp CRESTInterface; `search()` is the conformer-search entry used by ensemble/energy |
| xTB backend | `backends/xtb.py` | Delegates to cccp XTBInterface |
| CENSO backend | `backends/censo_backend.py` | Thin adapter → `cccp.qc.interfaces.censo.CensoInterface` (presets, rcfile gen, JSON/XYZ parsing live in cccp) |
| Isostat backend | `backends/isostat_backend.py` | Thin adapter → `cccp.qc.interfaces.isostat.IsostatInterface.cluster()` (title normalisation, env pinning in cccp) |
| Molclus backend | `backends/molclus_backend.py` | Thin adapter → `cccp.qc.interfaces.molclus.MolclusInterface.run_md()/search()` |
| External tools | `backends/external.py` + `external_backend.py` | run_shermo/batch_process_thermo re-exports; `cluster()` routes through `IsostatInterface` |
| IO wrapper | `io/structures.py` | StructureReader.detect_format/read, StructureWriter |
| RDKit embedding | `chem/embedding.py` | SMILES→3D, charge assignment, enumeration |
| Composition analysis | `chem/composition.py` | normalize_recalc_hess etc. |
| Intake models | `intake/models.py` | Data ingestion domain models |
| Intake parsers | `intake/parsers.py` | File parsing logic |
| Intake storage | `intake/storage.py` | Result storage |
| Ensemble workflow | `workflows/ensemble.py` | CREST → CENSO preset+screening |
| Energy workflow | `workflows/energy.py` | CENSO screening → cumulative-Boltzmann → DFT handoff |
| xTB-MD CENSO energy | `workflows/xtbmd_censo_energy.py` | GFN-FF MD → GFN1 batch opt → isostat → ewin filter → CENSO → fine DFT (Phase 1–5 done; multi-replica sampling in `workflows/xtbmd_md.py`, shared helpers in `workflows/energy_shared.py`) |
| Mechanism workflow | `workflows/mechanism.py` | TS search + IRC validation |
| Simple workflows | `workflows/simple.py` | singlepoint/opt/freq/optfreq/optfreqsp/scan/xtb-opt |
| Workflow registry | `workflows/registry.py` | CLI subcommand → WorkflowSpec builder mapping |
| API server | `api/server.py` | FastAPI app factory + static frontend hosting at `/` |
| API routes | `api/routes.py` | `/api/status`, `/api/backends`, `/api/workflows` |
| API v1 routes | `api/v1_routes.py` | Job submission, molecule upload, task mgmt (~1488 lines) |
| API schemas | `api/schemas.py` / `v1_schemas.py` | Pydantic models for status, backends, jobs |
| Scheduler jobs | `scheduler/jobs.py` | Job data models; `_derive_supported_workflows()` from active catalog |
| Job manager | `scheduler/manager.py` | Lifecycle management, polling, cancellation |
| Job runner | `scheduler/runner.py` | Background process execution |
| Task store | `scheduler/store.py` | Persistent (SQLite) job storage |
| Provenance | `scheduler/provenance.py` | Event sourcing, audit logging |
| Stage tasks | `scheduler/stage_tasks.py` | Plan providers mapping workflow → stage list |
| Remote execution | `scheduler/remote/` | LSF bsub/bjobs, SSH/SFTP pool, code sync, result fetch, cleanup |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` throughout
- **Docstrings**: Compact (single-line or short block), unlike verbose Google-style in `cccp/`
- **Backend design**: Capability Protocols (PEP 544) instead of monolithic ABC — backend declares what it can do
- **No chem logic in core/**: core/ contains only generic mechanism (Structure, WorkflowRunner, Registry)
- **Stage pipeline**: WorkflowSpec assembles Stage functions; WorkflowRunner executes sequentially
- **Backend layering**: workflows call `get_backend(...)(...)`, never the raw `cccp.qc.interfaces` classes directly
- **`__all__`**: Every `__init__.py` re-exports public symbols

## ANTI-PATTERNS
- **Thin wrapper syndrome**: acp/io and acp/core/config largely delegate to cccp — adds abstraction with minimal independent logic
- **Depends on cccp package**: acp imports from `cccp.config`, `cccp.io`, `cccp.qc.interfaces` — intentional coupling (cccp is the QC connection library)
- **`# pyright:` suppressions widespread**: ~14 acp/ files suppress type-checking rules; pyright is not in the project toolchain
- **bare `except Exception:`**: several instances across api/, chem/, cli.py, scheduler/ silently swallow errors
- **HARTREE_TO_KCAL duplication**: Defined in both `acp/core/models.py` and `cccp/utils/constants.py`
- **Retired catalog entries retained**: conformer/benchmark kept in `WORKFLOW_CATALOG` with `status:"retired"` + `visible:False` for historical-job display; do not re-add registry/CLI entries for them. **NMR was reactivated 2026-08-07 (P1a)** — it is now `status:"active"` with full CLI/registry/scheduler/frontend wiring (see `docs/ACP_NMR_DP4_DevDoc.md` appendix A).
