# Auto-Calc Platform (ACP) — Project Knowledge Base

**Generated:** 2026-08-28
**Branch:** refactor/calc-cleanup (post-refactor minimal architecture)

## OVERVIEW
Automated computational chemistry platform. Two-package architecture under `src/`: `cccp` (Computational Chemistry Connection Package — the QC interface library, formerly `conformer_search`) and the `acp` module (Phase 1+, calculation-plan-driven workflow pipeline, unified CLI). Python 3.10+, setuptools, YAML config.

**Active workflows (10, post-refactor 2026-08-28):** Confsearch, PESsearch, BatchOptimize, irc, scan, plus nmr (DP4/DP5) and simple (singlepoint/optimize/frequency/xtb-optimize). Retired (kept read-only in catalog for historical-job display): ensemble, energy, xtbmd_censo_energy, mechanism, conformer, benchmark, mech-conf, mech-step, mech-confirm, mech-chain, optfreq, optfreqsp, Lowconfirm, Highconfirm. Scheduler job lifecycle includes PAUSED (local SIGSTOP/SIGCONT, remote bstop/bresume) plus checkpoint continue, rerun, and cascade purge.

## STRUCTURE
```
ACP_V1_20260811/
├── src/cccp/  # Computational Chemistry Connection Package (QC interface library; reverse-synced 2026-07-13)
│   ├── config.py          # 6-source YAML config load/merge (756 lines; reads ~/.cccp.yaml, falls back to ~/.conformer_search.yaml)
│   ├── software.py        # Centralized QC executable resolution — resolve_executable()/require_executable()/detect_version()/discover_all() (config → CONFSEARCH_*_PATH env → PATH+Python env → legacy fallback)
│   ├── version.py         # __version__ (dup w/ __init__.py — bump BOTH)
│   ├── core/              # ConformerEngine (dormant, ~1770 lines), ProtocolSpec, CandidateSet, state_manager
│   ├── qc/interfaces/     # subprocess wrappers: ORCA/CREST/XTB/xtb_thermo + CENSO/ISOSTAT/Molclus (2026-08-02 consolidation — single subprocess layer); 2026-08 TS/IRC/scan wave added base.py (QCInterfaceBase) + constraints.py / orca_ts.py / xtb_path.py / xtb_scan.py (ORCAInterface/XTBInterface subclass QCInterfaceBase)
│   ├── qc/runners/        # run_isostat (DEPRECATED) / run_shermo / batch_process_thermo (all in __init__.py)
│   ├── qc/cluster/        # Local + LSF adapters + factory (single __init__.py)
│   ├── io/                # MolecularInputHandler — format detection, RDKit embedding (442 lines)
│   ├── pipeline/          # PipelineExecutor (thin, 12 lines)
│   └── utils/             # File I/O, geometry, constants, solvent maps
├── src/acp/               # Unified module (~130 .py, ~65k lines incl. API/scheduler/nmr)
│   ├── cli.py             # argparse subcommand CLI: `acp run Confsearch|PESsearch|BatchOptimize|irc|scan|nmr|serve|simple` (2608 lines)
│   ├── catalog.py         # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS (2915 lines — retired entries kept as status:"retired")
│   ├── confsearch/        # Unified conformer search: engine, contracts, manifest, profiles, selection, protocols/ (xtb-crest/xtb-md/censo-crest/xtbmd-censo), shared/
│   ├── calculations/      # Calculation-plan primitives and engines: contracts, checkpoint, executor, plans, primitives/ (sp/opt/freq/scan/irc/thermochemistry), pes/, batch/, irc/
│   ├── compat/            # Read-only legacy manifest readers and layout compatibility (legacy/ subpkg)
│   ├── results/           # Unified result manifest reader (result_manifest.json)
│   ├── storage/           # Unified v2 result manifest write (result_manifest.json schema)
│   ├── core/              # Shared mechanism: Structure, WorkflowRunner, Registry, State, Config
│   ├── backends/          # QC backends with capability Protocols — thin adapters only (no subprocess; see 2026-08-02 consolidation)
│   ├── chem/              # Chemistry: RDKit embedding, XYZ tools
│   ├── intake/            # Data ingestion: models, parsers (6 formats), storage
│   ├── io/                # StructureReader / StructureWriter (thin cccp wrapper)
│   ├── workflows/         # Legacy workflows (retired: ensemble, energy, xtbmd_censo_energy) + nmr, simple + registry
│   ├── nmr/               # Phase 3 NMR + DP4/DP5: models, averaging, probability, error_model, FCHL, spectra, report (12 modules + models/ data dir; see nmr/AGENTS.md)
│   ├── api/               # FastAPI — server, routes, v1_routes, v2_routes, schemas (~5000 lines)
│   └── scheduler/         # Task scheduler — jobs, manager, runner, store, provenance, artifacts, migrations, events, files, logs, projects, stage_tasks, local_cleanup, nodes, metrics, tasks + remote/ subpkg (16 files, ~7800 lines)
│       └── remote/        # Remote LSF execution: SSH pool, SFTP ops, code sync, bsub/bjobs, bstop/bresume, result fetch, cleanup, script_gen, node_manager, config (11 files, ~4900 lines; see remote/AGENTS.md)
├── frontend/              # ACP Workbench (v1 + v2) single-page dark dashboards
├── scripts/               # start_acp.sh, bootstrap_venv.sh, install_systemd.sh
├── config/defaults.yaml   # Default YAML config (may diverge from Python built-in — built-in is authoritative)
├── tests/                 # 96 test files (~1315 tests), conftest.py, fixtures/, baseline/ (audit artifact)
├── docs/                  # Dev docs: CENSO, NMR_DP4, xTBMD_CENSO, Mechanism Research, Job File Layout, Remote execution, Simple Workflows
├── requirements-node.txt  # Remote compute-node runtime deps (numpy/rdkit/pyyaml only — NOT pyproject); installed by NodeManager.bootstrap_node() and auto-synced by CodeSyncer. Add any new `acp run` runtime import HERE + pyproject.toml
└── pyproject.toml         # api/remote/nmr/dev optional deps; console script `acp = acp.cli:main`
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ACP CLI (unified entry) | `src/acp/cli.py` | `acp run Confsearch|PESsearch|BatchOptimize|irc|scan|nmr|serve|simple workflows` (2608 lines) |
| Add new protocol | `src/cccp/core/protocols.py` | Update `_get_default_protocol_config()` (626 lines) |
| Config loading | `src/cccp/config.py` | 6-source merge (756 lines) |
| Config defaults (ACP) | `src/acp/core/config.py` | ACP-specific config loading (169 lines) |
| ACP backends entry | `src/acp/backends/__init__.py` + `registry.py` | Protocol-based capability system |
| ORCA backend | `src/acp/backends/orca.py` | ACP adapter wrapping ORCAInterface |
| CREST backend | `src/acp/backends/crest.py` | ACP adapter wrapping CRESTInterface |
| xTB backend | `src/acp/backends/xtb.py` | ACP adapter wrapping XTBInterface |
| CENSO backend | `src/acp/backends/censo_backend.py` | Thin adapter → `cccp.qc.interfaces.censo.CensoInterface` (rcfile gen, presets, JSON/XYZ parsing live in cccp) |
| ISOSTAT backend | `src/acp/backends/isostat_backend.py` | Thin adapter → `cccp.qc.interfaces.isostat.IsostatInterface` (title normalisation, env pinning in cccp) |
| Molclus backend | `src/acp/backends/molclus_backend.py` | Thin adapter → `cccp.qc.interfaces.molclus.MolclusInterface` (md.inp/settings.ini/trajectory validation in cccp) |
| Legacy ORCA interface | `src/cccp/qc/interfaces/orca.py` | ORCA subprocess + TS opt / IRC / relaxed scan / constrained opt / NMR shielding (2153 lines; `NmrShieldingParser` + all TS/IRC/scan logic — the 2026-08 primitives split lives in `orca_ts.py`/`constraints.py`/`xtb_scan.py`/`xtb_path.py`) |
| Legacy CREST interface | `src/cccp/qc/interfaces/crest.py` | `CRESTInterface` conformer search only (362 lines; batch `-mdopt` moved to `XTBInterface.enso_thermo`) |
| Legacy xTB interface | `src/cccp/qc/interfaces/xtb.py` | `XTBInterface` — optimize / constrained_optimize / relaxed_scan / single_point / enso_thermo (583 lines) |
| ISOSTAT interface | `src/cccp/qc/interfaces/isostat.py` | Single ISOSTAT path (exit-24 title normalisation, error propagation, env pinning) |
| Molclus interface | `src/cccp/qc/interfaces/molclus.py` | xTB-MD + Molclus full pipeline (md.inp, settings.ini, search()) |
| CENSO interface | `src/cccp/qc/interfaces/censo.py` | CENSO subprocess: rcfile gen, preset injection, template injection, JSON/XYZ parsing |
| Legacy ISOSTAT/Shermo | `src/cccp/qc/runners/__init__.py` | `run_isostat()` (DEPRECATED — IsostatInterface is the single path), `run_shermo()`, `batch_process_thermo()` (342 lines) |
| Ensemble workflow | `src/acp/workflows/ensemble.py` | **RETIRED CLI entry** (Confsearch v1.0): CREST → CENSO P+S (398 lines); still live as Confsearch protocol engine (censo-crest/xtb-crest screen policy) |
| Energy workflow | `src/acp/workflows/energy.py` | **RETIRED CLI entry** (Confsearch v1.0): rank1-only default (753 lines); still live as Confsearch protocol engine (censo-crest rank1/cumulative-99) |
| xTB-MD CENSO energy | `src/acp/workflows/xtbmd_censo_energy.py` | **RETIRED CLI entry** (Confsearch v1.0): GFN-FF MD → CENSO (2080 lines); still live as Confsearch protocol engine (xtbmd-censo) |
| Shared energy helpers | `src/acp/workflows/energy_shared.py` | `resolve_levels`/`run_rank1_handoff`/`boltzmann_weights`/etc.; ORCA handoff via `get_backend("orca")` (2026-08-02) |
| NMR workflow | `src/acp/workflows/nmr.py` | Conformer search → GIAO → Boltzmann averaging (1056 lines) |
| Confsearch engine | `src/acp/confsearch/engine.py` | Unified conformer search + energies; protocols xtb-crest / xtb-md / censo-crest / xtbmd-censo |
| Confsearch contracts | `src/acp/confsearch/contracts.py` | Protocol-specific constraints and quality gates |
| Confsearch manifest | `src/acp/confsearch/manifest.py` | `confsearch_manifest.json` handoff artifact (S1) |
| Confsearch profiles | `src/acp/confsearch/profiles.py` | light / default / high resource profiles |
| Confsearch selection | `src/acp/confsearch/selection.py` | Candidate selection and ranking logic |
| Confsearch protocols | `src/acp/confsearch/protocols/` | xtb-crest / xtb-md / censo-crest / xtbmd-censo protocol implementations |
| PESsearch (S2) | `src/acp/workflows/pes_search.py` | Reaction path search + TS/intermediate guesses; output `RESULT/pes_search/` |
| BatchOptimize | `src/acp/calculations/batch/engine.py` | Per-item Opt/TS + frequency + SP + thermochemistry; profiles: opt_only/opt_freq/opt_freq_sp/opt_freq_sp_thermo |
| Batch input models | `src/acp/calculations/batch/models.py` | TAG parsing (`TAG: TS|INT | candidate_id=...`), `BatchStructureItem`/`BatchCalculationItem`/`BatchCalculationManifest`, loaders |
| IRC primitive | `src/acp/calculations/irc/` | `run_irc()` endpoint discovery + validation; `irc/contracts.py` + `irc/validation.py` (connectivity, fingerprint, RMSD, TS identity) |
| PES scan core | `src/acp/calculations/pes/scan.py` | Standalone relaxed-scan execution + candidate recommendation + BatchSinglePointExecutor integration |
| PES contracts | `src/acp/calculations/pes/contracts.py` | `PesScanRequest`, `ScanCoordinate`, `EnergyProfile`, `CandidateRecommendation` frozen dataclasses |
| PES engine | `src/acp/calculations/pes/engine.py` | `PesSearchEngine` orchestrator: confsearch manifest → scan → candidates → `RESULT/pes_search/` |
| PES path analysis | `src/acp/calculations/pes/path_analysis.py` | PathFrameEvidence, PathProfile, arclength, RMSD, energy derivatives |
| PES path selection | `src/acp/calculations/pes/path_selection.py` | SelectionPolicy, SeedSelection, select_path_seeds, replay_rescue_selection |
| PES validation | `src/acp/calculations/pes/validation.py` | Topology guards, bond graphs, risky contacts, scan trajectory validation |
| PES atom mapping | `src/acp/calculations/pes/atom_mapping.py` | RDKit MCS atom mapping, AtomIdentityMap (standalone, no mechanism dependency) |
| PES bond changes | `src/acp/calculations/pes/bond_changes.py` | BondChange, compute_bond_changes, suggest_coordinate_plan |
| Calculation contracts | `src/acp/calculations/contracts.py` | `CalculationPlan`, `CalculationRequest`, `CalculationStep`, `StructureArtifact`, `Checkpoint` frozen dataclasses |
| Plan builders | `src/acp/calculations/plans.py` | `build_simple_plan`, `build_batch_plan`, `build_irc_request` |
| Plan executor | `src/acp/calculations/executor.py` | `CalculationPlanExecutor` — step dispatch + coordinate handoff + checkpoint resume + manifest write |
| Calculation primitives | `src/acp/calculations/primitives/` | `run_singlepoint`, `run_optimize`, `run_frequency`, `run_scan`, `run_irc`, `ThermochemistryCalculator` |
| Checkpoint protocol | `src/acp/calculations/checkpoint.py` | `write_checkpoint` / `load_checkpoint` — atomic JSON with plan fingerprint validation |
| Compat legacy readers | `src/acp/compat/legacy/manifests.py` | Read-only adapters: `read_s2_path_manifest`, `read_s3_lowconfirm_manifest`, `read_s4_highconfirm_manifest`, etc. |
| Compat layout probing | `src/acp/compat/legacy/layouts.py` | `find_study_layout`, `find_reaction_json` — v2 + legacy dual-probe read-only resolution |
| Result manifest (read) | `src/acp/results/manifest.py` | Unified `result_manifest.json` reader |
| Result manifest (write) | `src/acp/storage/manifest.py` | Unified v2 `result_manifest.json` writer (design doc §8) |
| Scheduler tasks | `src/acp/scheduler/tasks.py` | Task-level scheduling for stage workflows |
| API v2 routes | `src/acp/api/v2_routes.py` | v2 API surface |
| Simple workflows | `src/acp/workflows/simple.py` | singlepoint/optimize/frequency/scan/xtb-opt (635 lines) |
| Workflow registry | `src/acp/workflows/registry.py` | Maps CLI subcommands → WorkflowSpec builders (153 lines) |
| Method catalog | `src/acp/catalog.py` | METHOD_META dict: methods, bases, route blocks (2915 lines) |
| Core data models | `src/acp/core/models.py` | Structure, StructureRecord, StructureEnsemble (389 lines) |
| Workflow engine | `src/acp/core/workflow.py` | WorkflowRunner, WorkflowSpec, Stage (105 lines) |
| Workflow state | `src/acp/core/state.py` | WorkflowState, EventLog (272 lines) |
| CENSO dev doc | `docs/ACP_CENSO_Integration_DevDoc.html` | Authoritative design + P1–P5 audit history (v14: acceptance passed) |
| Simple workflows doc | `docs/ACP_Simple_Workflows_DevDoc.html` | 5 ORCA simple workflow design |
| Job file layout spec | `docs/ACP_Job_File_Layout_Spec.md` | Authoritative job/work_dir file-layout contract (scheduler + frontend file tree) |
| Mechanism research doc | `docs/ACP_Mechanism_Research_DevDoc.md` | **RETIRED** mechanism study S0→S4 design (native-first, RPH parity); kept for reference only |
| Input parsing | `src/cccp/io/input_handler.py` | SMILES→RDKit embed; XYZ/GJF/LOG/OUT parse (442 lines) |
| ACP intake parsers | `src/acp/intake/parsers.py` | 6 format parsers: XYZ/SDF/MOL/GJF/INP/SMILES (565 lines) |
| RDKit embedding | `src/acp/chem/embedding.py` | SMILES→RDKit embed, charge assignment, XYZ tools (517 lines) |
| Constants / units | `src/cccp/utils/constants.py` | HARTREE_TO_KCAL, element masses (38 lines) |
| NMR models | `src/acp/nmr/models.py` | NmrConfig, ExperimentalNmr/Peak, ConformerShielding, NmrReport (385 lines) |
| NMR parsing | `src/acp/nmr/io.py` | `parse_experimental_nmr` — experimental shift file parser (140 lines) |
| NMR averaging | `src/acp/nmr/averaging.py` + `equivalence.py` | Boltzmann averaging, symmetry equivalence detection |
| NMR scaling/assignment | `src/acp/nmr/scaling.py` + `assignment.py` | Regression fit (incl. Goodman), atom↔peak matching |
| NMR DP4/DP5 | `src/acp/nmr/probability.py` + `error_model.py` | compute_dp4/dp5, GoodmanDP5Model, load_dp5_model |
| NMR FCHL kernels | `src/acp/nmr/fchl.py` | P4: FCHL atomic representations (qml extra, optional) |
| NMR spectra | `src/acp/nmr/spectra.py` | P3: Bruker experiment processing |
| NMR reports | `src/acp/nmr/report.py` | JSON + XLSX + plot serialization (nmr_report.json, nmr_assignment.xlsx) |
| NMR workflow | `src/acp/workflows/nmr.py` | Conformer search → GIAO → Boltzmann averaging → DP4/DP5 (1056 lines) |
| ACP API server | `src/acp/api/server.py` | FastAPI app factory + static frontend hosting at `/` (183 lines) |
| API routes | `src/acp/api/routes.py` | `/api/status`, `/api/backends` (379 lines) |
| API v1 routes | `src/acp/api/v1_routes.py` | Job submission, molecule upload, task management, job detail projection (3280 lines) |
| Structure sources | `src/acp/scheduler/structure_sources.py` | Reusable final structures from COMPLETED jobs (task-results tab). Discovery order: `RESULT/result_manifest.json` products (`kind: structure`/`xyz` — S2 candidates, batch S3/S4 outputs) → legacy `result_summary.json` → dedupe; entries carry `tag` (`TS`/`INT`) + `candidate_id` parsed from the XYZ TAG comment |
| Job detail endpoint | `src/acp/api/v1_routes.py` | `GET /api/v1/jobs/{id}/detail` — rich projection: job + stages (StageTaskStore, disk fallback) + artifacts_summary + error_detail/stderr_tail + disk_state + server-computed `recovery` matrix (pause/unpause/continue/rerun/purge buttons + notes); disk backfill of result when null (R1); POST `/jobs/{id}/pause` `/unpause` `/continue` `/rerun` + POST `/jobs/purge` |
| API schemas | `src/acp/api/schemas.py` / `v1_schemas.py` | Pydantic models for status, backends, jobs; incl. `V1JobDetailResponse`, `V1JobPurgeRequest/Response` (889 lines in v1_schemas.py) |
| ACP Workbench frontend | `frontend/ACP_Workbench.html` + `ACP_Workbench_v2.html` | Dark dashboard; polling /api/status, /api/backends; job detail view (stages stepper, error card, stderr tail, recovery action bar) + batch purge UI + paused badge + batch structure-source panel (`stage-batch-*`, Lowconfirm/Highconfirm) + results-list TAG badges + 载入全部候选 |
| Task scheduler | `src/acp/scheduler/` | 15 files: jobs, manager, runner, store, provenance, artifacts, migrations, events, files, logs, projects, stage_tasks, local_cleanup, nodes, metrics + remote/ |
| Job manager | `src/acp/scheduler/manager.py` | Job lifecycle management, polling, cancellation, pause/unpause/continue/rerun/purge (1636 lines) |
| Job queue ops (methods) | `src/acp/scheduler/manager.py` | `pause_job` (RUNNING→PAUSED; local killpg SIGSTOP, remote `bstop`) / `unpause_job` (PAUSED→RUNNING; local SIGCONT, remote `bresume`) / `continue_job` (FAILED/CANCELLED→QUEUED; mechanism phase-level + xtbmd stage-level checkpoint, `attempts`+1, `continued_from`; others raise ValueError) / `rerun_job` (enhanced clone → `{name}__rerun`, new job) / `purge_jobs` (batch cascade; active jobs require force_cancel) / `resume` (WAITING_REVIEW-review-only, DO NOT reuse) |
| Job runner | `src/acp/scheduler/runner.py` | Background process execution; `pause_local`/`resume_local` via killpg SIGSTOP/SIGCONT (1635 lines) |
| Provenance tracking | `src/acp/scheduler/provenance.py` | Event sourcing, audit logging |
| Data store | `src/acp/scheduler/store.py` | SQLite persistence |
| Remote LSF runner | `src/acp/scheduler/remote/runner.py` | bsub/bjobs/bkill, bstop/bresume mapping to PAUSED, state.json observation (1423 lines) |
| Remote SSH pool | `src/acp/scheduler/remote/ssh.py` | Thread-safe paramiko connection pool (333 lines) |
| Remote SFTP ops | `src/acp/scheduler/remote/sftp.py` | SFTP file transfer helpers (302 lines) |
| Remote exec config | `src/acp/scheduler/remote/config.py` | Remote-node config (253 lines) |
| Remote code sync | `src/acp/scheduler/remote/sync.py` | Incremental mtime-based sync to remote nodes (262 lines) |
| Remote monitor | `src/acp/scheduler/remote/monitor.py` | LSF job monitor, `bstop_job`/`bresume_job`, PSUSP/SSUSP/USUSP→paused map, disk check (404 lines) |
| Remote result fetch | `src/acp/scheduler/remote/fetcher.py` | On-demand SFTP file retrieval (480 lines) |
| Remote cleanup | `src/acp/scheduler/remote/cleanup.py` | Retention-based disk cleanup (514 lines) |
| Remote script gen | `src/acp/scheduler/remote/script_gen.py` | LSF submission script builder (554 lines) |
| Remote node mgr | `src/acp/scheduler/remote/node_manager.py` | Node status with 30s TTL cache (315 lines) |

## CONVENTIONS
- **Docstrings**: Google-style (`Args:`, `Returns:`, `Attributes:`) throughout
- **Type annotations**: Universal; legacy `cccp/` uses `typing.Optional[X]`, new `acp/` uses `X | None` with `from __future__ import annotations`
- **Imports**: stdlib → third-party (numpy, rdkit) → local (`from cccp...` or `from acp...`)
- **Logging**: `logger = logging.getLogger(__name__)` in every module
- **Dataclasses**: `@dataclass(frozen=True)` preferred for specs; mutable for data containers
- **ABC**: `cccp/` uses `ABC` base; `acp/` uses structural `Protocol` (PEP 544)
- **Paths**: `pathlib.Path` preferred over `os.path`
- **Linter/formatter configured**: ruff (E/F/I/N/W/UP), ruff-format, mypy (strict), pre-commit with ruff hooks
- **`__all__`**: All subpackage `__init__.py` files re-export public symbols
- **Job state machine** (2026-08-17 wave): statuses include `PAUSED`. `RUNNING ↔ PAUSED` via `pause_job`/`unpause_job` — local uses `os.killpg(pgid, SIGSTOP/SIGCONT)` (process stays in `_processes`; pause does NOT free memory/disk), remote uses LSF `bstop`/`bresume`. `PAUSED` is active (counted in queue, guarded against delete) but not terminal; the poller skips PAUSED like WAITING_REVIEW. Restart behavior: local PAUSED → killpg cleanup + FAILED with `[RESTART_FAILED] paused job frozen at restart — 可续算 (resumable via continue)`; remote PAUSED → re-adopted via `_try_recover_remote_job` (stays PAUSED). Cancel on PAUSED first SIGCONTs then SIGTERM→SIGKILL (frozen processes ignore SIGTERM).

## ANTI-PATTERNS (THIS PROJECT)
1. **NEVER import `pymatgen`** — removed from pyproject.toml (verified: 0 references in src/)
2. **NEVER update `__version__` in one place** — 3 sources: `cccp/__init__.py`, `cccp/version.py`, `pyproject.toml` (acp/__init__.py ALSO defines it — 4 sites total)
3. **NEVER change YAML defaults without updating Python built-in** — `config/defaults.yaml` vs `_get_default_config()` diverged historically
4. **NEVER add protocol to YAML `protocols` section** — unreachable; edit `_get_default_protocol_config()` in `protocols.py`
5. **NEVER put implementation in `__init__.py`** — remaining: `qc/cluster/__init__.py` (factory) + `qc/runners/__init__.py` (whole module)
6. **`__main__.py` exists for acp ONLY** — `python -m acp` and `python -m acp.cli` work; `python -m cccp` does NOT (no cccp/__main__.py — deleted in the rename)
7. **Two annotation styles coexist** — `typing.Optional[X]` in legacy cccp interface layer vs PEP 604 (`X | None`) in acp/ and newer cccp modules (config.py, software.py, protocols.py)
8. **`CRESTInterface` has no base class** — `class CRESTInterface:` (reverse-sync restored upstream form)
9. **`# pyright:` suppressions heavy in acp/** — 42 files (nmr/ is the densest); pyright not in toolchain
10. **`except Exception:` bare catches** — 80 sites across 25 files (worst: `api/v1_routes.py` (20), `chem/embedding.py` (10), `nmr/enumerate.py` (7))
11. **`HARTREE_TO_KCAL` duplicated** — `acp/core/models.py` AND `cccp/utils/constants.py` (identical value 627.5094740631)
12. **Gas-constant R duplicated 3×** — `cccp/core/candidates.py:130` (0.001987204), `cccp/core/engine.py:1726` (0.0019872041), `acp/workflows/nmr.py:379` (0.001987204259) — different precision!
13. **`conformer-search` console_script removed** — the legacy CLI (`cccp/cli.py`, `cccp/__main__.py`) was deleted; use `acp` instead. `bin/conformer-search` never existed on disk either.
14. **`XTBInterface` now standalone** — was co-located in `crest.py`, split to `qc/interfaces/xtb.py` in Phase C (2026-07-27)
15. **CI is disabled** — `.github/workflows/ci.yml` only triggers on `workflow_dispatch`; push/pull_request commented out. systemd unit `/etc/systemd/system/acp.service` is generated (not version-controlled) — edit `scripts/install_systemd.sh`, not the unit
16. **NEVER default mechanism provider `work_root` to `tempfile`** — S1/S2/S3/S4 QC artifacts must land under `study_dir/calc/` (s1/s1_xtbfast/s2/s2_peb/s3s4 subdirs, threaded via `build_study_providers(work_root=...)`); the `/tmp` defaults caused invisible frontend trees + unrecoverable remote-node leakage (fixed 2026-08-17; fallback is now `Path.cwd()/"acp_calc"`). Numbered run dirs (`ensemble_NNN`, `*__scan_NNN`) resume via `_helpers.next_sequence` disk scan
17. **ORCA `%geom` keyword is `Trust`, NOT `TrustRadius`** — `TrustRadius` is rejected at input parse (ORCA 5.x/6.x) killing every TS opt attempt; fixed 2026-08-17 in `orca_ts.ts_geom_block` (also emits `MaxIter` from `max_cycles`/`geom_maxiter`)
18. **`simple.py::_SCHEDULER_MARKERS` must list every scheduler pre-created file** (`events.jsonl`/`job.json`/`stdout.log`/`stderr.log`/`mechanism_config.json`/`task.json`/`input.xyz`/`WORK`/`RESULT`/`metrics.json` + `.exit_code`/`submit.lsf`/`input_source.json`) — otherwise `_resolve_output_dir` redirects scheduler jobs to a `<work_dir>_1/` sibling invisible to the file tree (fixed 2026-08-17; v1.2 2026-08-23 removed the retired lowercase `inputs/work/results` scaffolding entries)
19. **NEVER add a job status without updating the full surface** — `jobs.py::JobStatus` `is_active`/`is_terminal`, `store.counts()` consumers (`schemas.py::QueueCounts` + `routes.py`), and frontend `getStatusClass`/`getQueueCounts`/`isActiveJobStatus`/i18n (zh + en) must ALL recognize the new status (PAUSED 2026-08-17 touched every one of these)
20. **NEVER delete a job via bare `DELETE FROM jobs`** — use `store.purge_cascade` (`manager._purge_job_records`); `decision_points` links via `study_id` (no `job_id` column, subselect through `mechanism_studies`), and no FK cascades exist in the schema
21. **`resume(job_id, resolution)` is WAITING_REVIEW-review-only** — pause/unpause/continue are separate methods (`pause_job`/`unpause_job`/`continue_job`); non-requeue `resume()` has the RUNNING-bounce footgun (sets RUNNING with no thread → `exit_code 77` persists → bounces back to WAITING_REVIEW on next poll)
22. **NEVER put `job_id` (or any timestamp) into a disk path** — v1.2 (2026-08-23) contract: scheduler task dirs are named `<molecule>_<task>_<remark>` via `JobSpec.task_dir_name()`/`sanitize_task_dir_name`, deduped `__NN` by manager `_dedupe_task_dir`; `job_id` lives ONLY in DB PK, `job.json`, `task.json`, `WORK/00_RUNTIME` log headers, and `events.jsonl`. Workflow-side scheduler detection is `workflows/_helpers.is_scheduler_task_dir` (`job.json`+`task.json`) — in scheduler context workflows write at task ROOT (flat, no `{safe_name}/` nesting); non-scheduler CLI multi-molecule runs keep `{output}/{safe_name}/`. Old `{job_id}/`-named dirs stay read-compatible via `find_workflow_state` (shallow-first rglob) + DB `work_dir` authority. See `docs/ACP_Job_File_Layout_Spec.md` §1a/§2a/§6a
23. **mechanism 布局已归一化（v1.2，2026-08-23）** — `mechanism_study/<study_id>/` 第三套命名体系废除：新任务产物落 `WORK/{02_SEARCH,03_OPT/TS,07_PATH,08_ANALYSIS}` + `RESULT/mechanism`，`study_id` 仅存 DB/指纹。所有路径读写经 `acp/mechanism/layout.py`（`resolve_study_layout` 写 / `find_study_layout` 双探针读 / `find_reaction_json` 预运行探测）；`study.study_dir` 持久化字段 = `WORK/08_ANALYSIS`。历史任务经 `LEGACY_FALLBACK_ENABLED` 只读兼容，**任何新代码不得再直接拼 `mechanism_study/<id>/` 路径**（grep 校验：字面量只允许出现在 layout.py）
24. **calculations/ 是计算基元的唯一驻点** — `src/acp/calculations/` 包含所有 QC 计算原语（sp/opt/freq/scan/irc/thermochemistry）、计划构建器（plans.py）、执行器（executor.py）、合同数据类（contracts.py）、以及 pes/batch/irc 子包。**任何新计算能力必须加入 calculations/primitives/，不得散落在 workflows/ 或 backends/ 中。** `CalculationsPlanExecutor` 是 single-item 单步调度的唯一入口；多项目调度走 BatchOptimizeEngine。
25. **compat/ 是遗留布局的只读兼容层** — `src/acp/calculations/compat/legacy/` 提供历史 manifest 读取器（`read_s2_path_manifest` 等）和布局探测（`find_study_layout`）。**只读，禁止写入。** 新代码消费数据必须通过 `acp.results.manifest`（v2 格式），仅在需要兼容历史任务时经由 compat 转接。

## UNIQUE STYLES
- Module docstrings: title + `====` underline + `Author: QCcalc Team` (38 files, mostly cccp/)
- ACP backends use capability Protocols (GeometryOptimizer, SinglePointCalculator, etc.) instead of ABC — EXCEPT `QCBackend(ABC)` (backends/base.py) and `ErrorModel(ABC)` (nmr/error_model.py)
- `CRESTInterface` has no base class (`class CRESTInterface:`); `XTBInterface` standalone in `xtb.py`
- Type annotation style split: legacy cccp uses `typing.X`; acp + newer cccp use `X | None` with `from __future__ import annotations` (133 of 160 .py files use the future import)
- `logger = logging.getLogger(__name__)` in every module (86 files); pathlib.Path only — zero `os.path.*` usage; `os.replace` for atomic writes
- Scheduler launches jobs as `python -m acp.cli run <workflow>` subprocesses (undeclared but production-critical entry form)

## COMMANDS
```bash
# Install
pip install -e .
pip install -e '.[dev]'          # adds pytest + pytest-cov
pip install -e '.[api]'          # adds fastapi + uvicorn
pip install -e '.[remote]'       # adds paramiko (SSH/SFTP)

# Run (new ACP entry — recommended)
# Confsearch — unified conformer search + energies (4 protocols)
acp run Confsearch --input "CCO" --protocol xtb-crest --refinement-policy screen --output ./out
acp run Confsearch --input "CCO" --protocol censo-crest --profile light --output ./out
acp run Confsearch --input "CCO" --protocol xtbmd-censo --refinement-policy rank1 --output ./out
acp run Confsearch --input "CCO" --protocol xtb-md --refinement-policy cumulative-99 --output ./out

# PESsearch — reaction path search from Confsearch manifest (S2)
acp run PESsearch --from-job 20260823_001_Confsearch --output ./pes_out
acp run PESsearch --from-artifact RESULT/confsearch/confsearch_manifest.json --output ./pes_out

# BatchOptimize — per-item Opt/TS + frequency + SP + thermochemistry
acp run BatchOptimize --from-job 20260823_002_PESsearch --output ./batch_out
acp run BatchOptimize --items-file structures.xyz --profile opt_freq_sp_thermo --output ./batch_out

# IRC — endpoint discovery + validation
acp run irc --input ts_structure.xyz --output ./irc_out

# Scan — relaxed coordinate scan
acp run scan --input "CCO" --coordinate 3,4,1.0,3.0 --output ./scan_out

# NMR chemical shift prediction
acp run nmr --input "CCO" --output ./nmr_results
acp run nmr --input "CCO" --backend orca --reference "13C=185.0" "1H=31.5"

# simple ORCA workflows
acp run singlepoint --input "CCO" --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c"
acp run frequency --input molecule.xyz

# web server
acp run serve --port 8765

# Retired workflows (historical-job display only):
# acp run ensemble  → Confsearch + censo-crest + screen
# acp run energy    → Confsearch + censo-crest + rank1/cumulative-99
# acp run xtbmd_censo_energy → Confsearch + xtbmd-censo
# acp run mechanism → PESsearch / Lowconfirm / Highconfirm (split into 3 stages)
# acp run Lowconfirm → BatchOptimize(opt_freq) + irc
# acp run Highconfirm → BatchOptimize(opt_freq_sp_thermo) + irc
# acp run optfreq → optimize + frequency (or BatchOptimize)
# acp run optfreqsp → BatchOptimize

# Test (real-binary tests are collection-skipped; use --run-slow to include)
pytest tests/ -v
pytest tests/ --run-slow -v
pytest -m "not slow" -v
pytest tests/test_acp_workflows_energy.py -v
pytest tests/test_acp_workflows_xtbmd_censo_energy.py -v
pytest tests/test_acp_mechanism_study.py -v
pytest tests/test_acp_workflows_nmr.py -v
pytest tests/test_acp_nmr_probability.py -v
pytest tests/test_acp_backends.py -v
pytest tests/test_acp_censo_p5_acceptance.py -v
pytest tests/test_remote_phase1.py -v

# Lint/format (CI gates; pre-commit hooks = ruff --fix + ruff-format)
ruff check src tests
ruff format --check src tests
```

## SYSTEMD SERVICE
- **Service name**: `acp.service`
- **Config file**: `/etc/systemd/system/acp.service`
- **Runs as**: user `<user>`
- **URL**: http://localhost:8765
- **Logs**: `sudo journalctl -u acp -f`
- **Reload reminder**: After any code modification, run `sudo systemctl restart acp`. The service does **not** use `--reload`, so a manual restart is required.
