# acp/ — ACP Unified Module

## OVERVIEW
The unified `acp` CLI, stage-based workflow pipeline, capability-driven QC backends, and generic core models. ~130 files, ~65k lines (incl. API + scheduler + nmr). Coexists with the underlying `cccp` package (Computational Chemistry Connection Package — the QC interface library).

**Active workflows (10, post-refactor 2026-08-28):** Confsearch, PESsearch, BatchOptimize, irc, scan, plus nmr (DP4/DP5) and simple (singlepoint/optimize/frequency/xtb-optimize). Retired (catalog `status:"retired"`, read-only for historical-job display): ensemble, energy, xtbmd_censo_energy, mechanism, conformer, benchmark, mech-conf, mech-step, mech-confirm, mech-chain, optfreq, optfreqsp, Lowconfirm, Highconfirm.

## STRUCTURE
```
acp/
├── cli.py              # `acp run {Confsearch|PESsearch|BatchOptimize|irc|scan|nmr|serve|simple workflows}` (~2608 lines)
├── __init__.py          # Package docstring only
├── __main__.py          # `python -m acp` works
├── catalog.py           # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS (2915 lines — retired entries kept as status:"retired")
├── confsearch/          # Unified conformer search: engine, contracts, manifest, profiles, selection, protocols/ (xtb-crest/xtb-md/censo-crest/xtbmd-censo), shared/
├── calculations/        # Calculation-plan primitives and engines: contracts, checkpoint, executor, plans, primitives/ (sp/opt/freq/scan/irc/thermochemistry), pes/, batch/, irc/
├── compat/              # Read-only legacy manifest readers and layout compatibility (legacy/ subpkg)
├── results/             # Unified result manifest reader (result_manifest.json)
├── storage/             # Unified v2 result manifest write (result_manifest.json schema)
├── core/                # Generic mechanism: Structure, WorkflowRunner, Registry, State, Config
├── backends/            # Capability-Protocol QC adapters (ORCA/CREST/xTB/CENSO/Isostat/Molclus/external)
├── chem/                # Chemistry: RDKit embedding, composition analysis
├── intake/              # Data ingestion: models, parsers, storage
├── io/                  # StructureReader / StructureWriter (thin cccp wrapper)
├── workflows/           # Legacy workflows (retired: ensemble, energy, xtbmd_censo_energy) + nmr, simple + registry
├── nmr/                 # Phase 3 NMR + DP4/DP5 (13 modules + models/ data dir; see nmr/AGENTS.md)
├── api/                 # FastAPI — server, routes, v1_routes, v2_routes, schemas (~5000 lines)
└── scheduler/           # Task scheduler — jobs, manager, runner, store, provenance, stage_tasks, tasks, ... + remote/ (16 + 11 files, ~7800 lines; see scheduler/AGENTS.md)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ACP CLI entry | `cli.py` | `acp run {Confsearch\|PESsearch\|BatchOptimize\|irc\|scan\|serve\|nmr\|simple workflows}` dispatch (~2608 lines) |
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
| External tools | `backends/external.py` + `external_backend.py` | Shermo/batch_process_thermo re-exports; `cluster()` routes through `IsostatInterface` |
| IO wrapper | `io/structures.py` | StructureReader.detect_format/read, StructureWriter |
| RDKit embedding | `chem/embedding.py` | SMILES→3D, charge assignment, enumeration |
| Composition analysis | `chem/composition.py` | normalize_recalc_hess etc. |
| Intake models | `intake/models.py` | Data ingestion domain models |
| Intake parsers | `intake/parsers.py` | File parsing logic |
| Intake storage | `intake/storage.py` | Result storage |
| Confsearch engine | `confsearch/engine.py` | Unified conformer search + energies; protocols xtb-crest / xtb-md / censo-crest / xtbmd-censo |
| Confsearch manifest | `confsearch/manifest.py` | `confsearch_manifest.json` handoff artifact (S1) |
| Confsearch protocols | `confsearch/protocols/` | xtb-crest / xtb-md / censo-crest / xtbmd-censo protocol implementations |
| Calculation contracts | `calculations/contracts.py` | `CalculationPlan`, `CalculationRequest`, `CalculationStep`, `StructureArtifact`, `Checkpoint` frozen dataclasses |
| Plan builders | `calculations/plans.py` | `build_simple_plan`, `build_batch_plan`, `build_irc_request` |
| Plan executor | `calculations/executor.py` | `CalculationPlanExecutor` — step dispatch + coordinate handoff + checkpoint resume + manifest write |
| Calculation primitives | `calculations/primitives/` | `run_singlepoint`, `run_optimize`, `run_frequency`, `run_scan`, `run_irc`, `ThermochemistryCalculator` |
| Checkpoint protocol | `calculations/checkpoint.py` | `write_checkpoint` / `load_checkpoint` — atomic JSON with plan fingerprint validation |
| PES scan core | `calculations/pes/scan.py` | Standalone relaxed-scan execution + candidate recommendation + BatchSinglePointExecutor integration |
| PES engine | `calculations/pes/engine.py` | `PesSearchEngine` orchestrator: confsearch manifest → scan → candidates → `RESULT/pes_search/` |
| PES contracts | `calculations/pes/contracts.py` | `PesScanRequest`, `ScanCoordinate`, `EnergyProfile`, `CandidateRecommendation` frozen dataclasses |
| PES path analysis | `calculations/pes/path_analysis.py` | PathFrameEvidence, PathProfile, arclength, RMSD, energy derivatives |
| PES path selection | `calculations/pes/path_selection.py` | SelectionPolicy, SeedSelection, select_path_seeds, replay_rescue_selection |
| PES validation | `calculations/pes/validation.py` | Topology guards, bond graphs, risky contacts, scan trajectory validation |
| PES atom mapping | `calculations/pes/atom_mapping.py` | RDKit MCS atom mapping, AtomIdentityMap (standalone, no mechanism dependency) |
| PES bond changes | `calculations/pes/bond_changes.py` | BondChange, compute_bond_changes, suggest_coordinate_plan |
| BatchOptimize engine | `calculations/batch/engine.py` | `BatchOptimizeEngine` — per-item Opt/TS + frequency + SP + thermochemistry |
| Batch input models | `calculations/batch/models.py` | TAG parsing, `BatchStructureItem`/`BatchCalculationItem`/`BatchCalculationManifest`, loaders |
| IRC primitive | `calculations/irc/` | `run_irc()` endpoint discovery + validation; `irc/contracts.py` + `irc/validation.py` |
| Compat legacy readers | `compat/legacy/manifests.py` | Read-only adapters: `read_s2_path_manifest`, `read_s3_lowconfirm_manifest`, etc. |
| Compat layout probing | `compat/legacy/layouts.py` | `find_study_layout`, `find_reaction_json` — v2 + legacy dual-probe read-only resolution |
| Result manifest (read) | `results/manifest.py` | Unified `result_manifest.json` reader |
| Result manifest (write) | `storage/manifest.py` | Unified v2 `result_manifest.json` writer (design doc §8) |
| Scheduler tasks | `scheduler/tasks.py` | Task-level scheduling for stage workflows |
| API v2 routes | `api/v2_routes.py` | v2 API surface |
| Ensemble workflow | `workflows/ensemble.py` | **RETIRED CLI entry** (Confsearch v1.0): CREST → CENSO P+S; still live as Confsearch protocol engine (censo-crest/xtb-crest screen policy) |
| Energy workflow | `workflows/energy.py` | **RETIRED CLI entry** (Confsearch v1.0): rank1-only default; still live as Confsearch protocol engine (censo-crest rank1/cumulative-99) |
| xTB-MD CENSO energy | `workflows/xtbmd_censo_energy.py` | **RETIRED CLI entry** (Confsearch v1.0): GFN-FF MD → CENSO; still live as Confsearch protocol engine (xtbmd-censo) |
| Simple workflows | `workflows/simple.py` | singlepoint/optimize/frequency/scan/xtb-opt |
| Workflow registry | `workflows/registry.py` | CLI subcommand → WorkflowSpec builder mapping |
| API server | `api/server.py` | FastAPI app factory + static frontend hosting at `/` |
| API routes | `api/routes.py` | `/api/status`, `/api/backends`, `/api/workflows` |
| API v1 routes | `api/v1_routes.py` | Job submission, molecule upload, task mgmt, stage workflows (~3280 lines) |
| API schemas | `api/schemas.py` / `v1_schemas.py` | Pydantic models for status, backends, jobs |
| Scheduler jobs | `scheduler/jobs.py` | Job data models; `_derive_supported_workflows()` from active catalog; E7 CLI-flag helpers (censo_preset_from_method/xtbmd_method_flags) |
| Job manager | `scheduler/manager.py` | Lifecycle management, polling, cancellation |
| Job runner | `scheduler/runner.py` | Background process execution (`python -m acp.cli run <workflow>`) |
| Task store | `scheduler/store.py` | Persistent (SQLite) job storage |
| Provenance | `scheduler/provenance.py` | Event sourcing, audit logging |
| Stage tasks | `scheduler/stage_tasks.py` | Plan providers mapping workflow → stage list |
| Remote execution | `scheduler/remote/` | LSF bsub/bjobs, SSH/SFTP pool, code sync, result fetch, cleanup (see remote/AGENTS.md) |
| NMR package | `nmr/` | DP4/DP5, averaging, scaling, FCHL, spectra, report (see nmr/AGENTS.md) |

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
- **Retired catalog entries retained**: conformer/benchmark/ensemble/energy/xtbmd_censo_energy/mechanism/mech-conf/mech-step/mech-confirm/mech-chain/optfreq/optfreqsp/Lowconfirm/Highconfirm kept in `WORKFLOW_CATALOG` with `status:"retired"` + `visible:False` for historical-job display; do not re-add registry/CLI entries for them. **NMR was reactivated 2026-08-07 (P1a)** — it is now `status:"active"` with full CLI/registry/scheduler/frontend wiring (see `docs/ACP_NMR_DP4_DevDoc.md` appendix A).
