# Auto-Calc Platform (ACP) — Project Knowledge Base

**Generated:** 2026-08-12
**Branch:** N/A (not a git repo)

## OVERVIEW
Automated computational chemistry platform. Two-package architecture under `src/`: `cccp` (Computational Chemistry Connection Package — the QC interface library, formerly `conformer_search`) and the `acp` module (Phase 1+, stage-based workflow pipeline, unified CLI). CREST → CENSO → DFT (ORCA) → Shermo thermo. Python 3.10+, setuptools, YAML config. Active workflows: ensemble, energy, xtbmd_censo_energy, mechanism, nmr (DP4/DP5, reactivated 2026-08-07), simple (singlepoint/opt/freq/optfreq/optfreqsp/scan/xtb-opt). conformer/benchmark retired 2026-07-27.

## STRUCTURE
```
ACP_V1_20260811/
├── src/cccp/  # Computational Chemistry Connection Package (QC interface library; reverse-synced 2026-07-13)
│   ├── config.py          # 6-source YAML config load/merge (756 lines; reads ~/.cccp.yaml, falls back to ~/.conformer_search.yaml)
│   ├── software.py        # Centralized QC executable resolution — resolve_executable()/require_executable()/detect_version()/discover_all() (config → CONFSEARCH_*_PATH env → PATH+Python env → legacy fallback)
│   ├── version.py         # __version__ (dup w/ __init__.py — bump BOTH)
│   ├── core/              # ConformerEngine (dormant, ~1900 lines), ProtocolSpec, CandidateSet, state_manager
│   ├── qc/interfaces/     # subprocess wrappers: ORCA/CREST/XTB/xtb_thermo + CENSO/ISOSTAT/Molclus (2026-08-02 consolidation — single subprocess layer)
│   ├── qc/runners/        # run_isostat (DEPRECATED) / run_shermo / batch_process_thermo (all in __init__.py)
│   ├── qc/cluster/        # Local + LSF adapters + factory (single __init__.py)
│   ├── io/                # MolecularInputHandler — format detection, RDKit embedding (425 lines)
│   ├── pipeline/          # PipelineExecutor (thin, 78 lines)
│   └── utils/             # File I/O, geometry, constants, solvent maps
├── src/acp/               # Unified module (~52 .py, ~13k lines incl. API/scheduler/nmr)
│   ├── cli.py             # argparse subcommand CLI: `acp run ensemble|energy|xtbmd_censo_energy|nmr|mechanism|serve|simple|...` (2050 lines)
│   ├── catalog.py         # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS (2588 lines — largest file in project)
│   ├── core/              # Shared mechanism: Structure, WorkflowRunner, Registry, State, Config
│   ├── backends/          # QC backends with capability Protocols — thin adapters only (no subprocess; see 2026-08-02 consolidation)
│   ├── chem/              # Chemistry: RDKit embedding, XYZ tools
│   ├── intake/            # Data ingestion: models, parsers (6 formats), storage
│   ├── io/                # StructureReader / StructureWriter (thin cccp wrapper)
│   ├── workflows/         # ensemble, energy, xtbmd_censo_energy, mechanism, nmr, simple + registry (conformer/benchmark removed)
│   ├── nmr/               # Phase 3 NMR + DP4/DP5: models, averaging, probability, error_model, FCHL, spectra, report (13 modules + models/ data dir; see nmr/AGENTS.md)
│   ├── api/               # Phase 2 FastAPI — server, routes, v1_routes, schemas (~1600 lines)
│   └── scheduler/         # Phase 2 task scheduler — jobs, manager, runner, store, provenance, stage_tasks, ... + remote/ subpkg (15 files, ~5600 lines)
│       └── remote/        # Remote LSF execution: SSH/SFTP pool, code sync, bsub/bjobs, result fetch, cleanup, script_gen, node_manager (11 files, ~3900 lines; see remote/AGENTS.md)
├── frontend/              # ACP Workbench (v1 + v2) single-page dark dashboards
├── scripts/               # start_acp.sh, bootstrap_venv.sh, install_systemd.sh
├── config/defaults.yaml   # Default YAML config (may diverge from Python built-in — built-in is authoritative)
├── tests/                 # 60 test files (1047 tests), conftest.py, fixtures/, baseline/ (audit artifact)
├── docs/                  # Dev docs: CENSO, NMR_DP4, xTBMD_CENSO, Remote execution, Simple Workflows
└── pyproject.toml         # api/remote/nmr/dev optional deps; console script `acp = acp.cli:main`
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ACP CLI (unified entry) | `src/acp/cli.py` | `acp run ensemble|energy|xtbmd_censo_energy|mechanism|serve|singlepoint|opt|freq|scan|xtb-opt` (~2000 lines) |
| Add new protocol | `src/cccp/core/protocols.py` | Update `_get_default_protocol_config()` (610 lines) |
| Config loading | `src/cccp/config.py` | 6-source merge (677 lines) |
| Config defaults (ACP) | `src/acp/core/config.py` | ACP-specific config loading (145 lines) |
| ACP backends entry | `src/acp/backends/__init__.py` + `registry.py` | Protocol-based capability system |
| ORCA backend | `src/acp/backends/orca.py` | ACP adapter wrapping ORCAInterface |
| CREST backend | `src/acp/backends/crest.py` | ACP adapter wrapping CRESTInterface |
| xTB backend | `src/acp/backends/xtb.py` | ACP adapter wrapping XTBInterface |
| CENSO backend | `src/acp/backends/censo_backend.py` | Thin adapter → `cccp.qc.interfaces.censo.CensoInterface` (rcfile gen, presets, JSON/XYZ parsing live in cccp) |
| ISOSTAT backend | `src/acp/backends/isostat_backend.py` | Thin adapter → `cccp.qc.interfaces.isostat.IsostatInterface` (title normalisation, env pinning in cccp) |
| Molclus backend | `src/acp/backends/molclus_backend.py` | Thin adapter → `cccp.qc.interfaces.molclus.MolclusInterface` (md.inp/settings.ini/trajectory validation in cccp) |
| Legacy ORCA interface | `src/cccp/qc/interfaces/orca.py` | Subprocess via direct ORCA invocation (811 lines) |
| Legacy CREST interface | `src/cccp/qc/interfaces/crest.py` | `CRESTInterface` conformer search + batch `-mdopt` (723 lines) |
| Legacy xTB interface | `src/cccp/qc/interfaces/xtb.py` | `XTBInterface` — optimize / single_point / enso_thermo (338 lines) |
| ISOSTAT interface | `src/cccp/qc/interfaces/isostat.py` | Single ISOSTAT path (exit-24 title normalisation, error propagation, env pinning) |
| Molclus interface | `src/cccp/qc/interfaces/molclus.py` | xTB-MD + Molclus full pipeline (md.inp, settings.ini, search()) |
| CENSO interface | `src/cccp/qc/interfaces/censo.py` | CENSO subprocess: rcfile gen, preset injection, template injection, JSON/XYZ parsing |
| Legacy ISOSTAT/Shermo | `src/cccp/qc/runners/__init__.py` | `run_isostat()` (DEPRECATED — IsostatInterface is the single path), `run_shermo()`, `batch_process_thermo()` (323 lines) |
| Ensemble workflow | `src/acp/workflows/ensemble.py` | `acp run ensemble` — CREST → CENSO P+S (508 lines) |
| Energy workflow | `src/acp/workflows/energy.py` | `acp run energy` — rank1-only default (`--full-ensemble` restores Boltzmann ≥99%), `--levels`, opt/freq same-level (~740 lines; heavy helpers in energy_shared.py) |
| xTB-MD CENSO energy | `src/acp/workflows/xtbmd_censo_energy.py` | `acp run xtbmd_censo_energy` — GFN-FF MD → GFN1 batch opt → isostat → ewin filter → CENSO → fine DFT + G_total (2080 lines); multi-replica sampling in `xtbmd_md.py`; shared helpers in `energy_shared.py` |
| Shared energy helpers | `src/acp/workflows/energy_shared.py` | `resolve_levels`/`run_rank1_handoff`/`boltzmann_weights`/`select_cumulative_boltzmann`/`build_ensemble_summary`/`write_final_outputs`/`censo_record_to_candidate`/`xtb_passthrough_result`/`resolve_solvent_config`/`resolve_crest_ewin` (E4 extraction); ORCA handoff via `get_backend("orca")` (2026-08-02) |
| NMR workflow | `src/acp/workflows/nmr.py` | Conformer search → GIAO → Boltzmann averaging (502 lines) |
| Mechanism workflow | `src/acp/workflows/mechanism.py` | TS search + IRC validation (394 lines) |
| Simple workflows | `src/acp/workflows/simple.py` | singlepoint/opt/freq/optfreq/optfreqsp/scan/xtb-opt (546 lines) |
| Workflow registry | `src/acp/workflows/registry.py` | Maps CLI subcommands → WorkflowSpec builders (162 lines) |
| Method catalog | `src/acp/catalog.py` | METHOD_META dict: methods, bases, route blocks (2588 lines) |
| Core data models | `src/acp/core/models.py` | Structure, StructureRecord, StructureEnsemble (389 lines) |
| Workflow engine | `src/acp/core/workflow.py` | WorkflowRunner, WorkflowSpec, Stage (105 lines) |
| Workflow state | `src/acp/core/state.py` | WorkflowState, EventLog (272 lines) |
| CENSO dev doc | `docs/ACP_CENSO_Integration_DevDoc.html` | Authoritative design + P1–P5 audit history (v14: acceptance passed) |
| Simple workflows doc | `docs/ACP_Simple_Workflows_DevDoc.html` | 5 ORCA simple workflow design |
| Input parsing | `src/cccp/io/input_handler.py` | SMILES→RDKit embed; XYZ/GJF/LOG/OUT parse (425 lines) |
| ACP intake parsers | `src/acp/intake/parsers.py` | 6 format parsers: XYZ/SDF/MOL/GJF/INP/SMILES (565 lines) |
| RDKit embedding | `src/acp/chem/embedding.py` | SMILES→RDKit embed, charge assignment, XYZ tools (381 lines) |
| Constants / units | `src/cccp/utils/constants.py` | HARTREE_TO_KCAL, element masses (37 lines) |
| NMR models | `src/acp/nmr/models.py` | NmrConfig, ExperimentalNmr/Peak, ConformerShielding, NmrReport (385 lines) |
| NMR parsing | `src/acp/nmr/io.py` | `parse_experimental_nmr` — experimental shift file parser (140 lines) |
| NMR averaging | `src/acp/nmr/averaging.py` + `equivalence.py` | Boltzmann averaging, symmetry equivalence detection |
| NMR scaling/assignment | `src/acp/nmr/scaling.py` + `assignment.py` | Regression fit (incl. Goodman), atom↔peak matching |
| NMR DP4/DP5 | `src/acp/nmr/probability.py` + `error_model.py` | compute_dp4/dp5, GoodmanDP5Model, load_dp5_model |
| NMR FCHL kernels | `src/acp/nmr/fchl.py` | P4: FCHL atomic representations (qml extra, optional) |
| NMR spectra | `src/acp/nmr/spectra.py` | P3: Bruker experiment processing |
| NMR reports | `src/acp/nmr/report.py` | JSON + XLSX + plot serialization (nmr_report.json, nmr_assignment.xlsx) |
| NMR workflow | `src/acp/workflows/nmr.py` | Conformer search → GIAO → Boltzmann averaging → DP4/DP5 (502 lines) |
| ACP API server | `src/acp/api/server.py` | FastAPI app factory + static frontend hosting at `/` (183 lines) |
| API routes | `src/acp/api/routes.py` | `/api/status`, `/api/backends` (379 lines) |
| API v1 routes | `src/acp/api/v1_routes.py` | Job submission, molecule upload, task management (1488 lines) |
| API schemas | `src/acp/api/schemas.py` / `v1_schemas.py` | Pydantic models for status, backends, jobs |
| ACP Workbench frontend | `frontend/ACP_Workbench.html` + `ACP_Workbench_v2.html` | Dark dashboard; polling /api/status, /api/backends |
| Task scheduler | `src/acp/scheduler/` | 15 files: jobs, manager, runner, store, provenance, artifacts, migrations, events, files, logs, projects, stage_tasks, local_cleanup, nodes + remote/ |
| Job manager | `src/acp/scheduler/manager.py` | Job lifecycle management, polling, cancellation (1035 lines) |
| Job runner | `src/acp/scheduler/runner.py` | Background process execution (1251 lines) |
| Provenance tracking | `src/acp/scheduler/provenance.py` | Event sourcing, audit logging |
| Data store | `src/acp/scheduler/store.py` | SQLite persistence |
| Remote LSF runner | `src/acp/scheduler/remote/runner.py` | bsub/bjobs/bkill, state.json observation (1213 lines) |
| Remote SSH pool | `src/acp/scheduler/remote/ssh.py` | Thread-safe paramiko connection pool (323 lines) |
| Remote code sync | `src/acp/scheduler/remote/sync.py` | Incremental mtime-based sync to remote nodes (261 lines) |
| Remote monitor | `src/acp/scheduler/remote/monitor.py` | LSF job monitor, disk check (341 lines) |
| Remote result fetch | `src/acp/scheduler/remote/fetcher.py` | On-demand SFTP file retrieval (464 lines) |
| Remote cleanup | `src/acp/scheduler/remote/cleanup.py` | Retention-based disk cleanup (514 lines) |
| Remote script gen | `src/acp/scheduler/remote/script_gen.py` | LSF submission script builder (360 lines) |
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

## ANTI-PATTERNS (THIS PROJECT)
1. **NEVER import `pymatgen`** — removed from pyproject.toml (verified: 0 references in src/)
2. **NEVER update `__version__` in one place** — 3 sources: `cccp/__init__.py`, `cccp/version.py`, `pyproject.toml` (acp/__init__.py ALSO defines it — 4 sites total)
3. **NEVER change YAML defaults without updating Python built-in** — `config/defaults.yaml` vs `_get_default_config()` diverged historically
4. **NEVER add protocol to YAML `protocols` section** — unreachable; edit `_get_default_protocol_config()` in `protocols.py`
5. **NEVER put implementation in `__init__.py`** — remaining: `qc/cluster/__init__.py` (factory) + `qc/runners/__init__.py` (whole module)
6. **`__main__.py` exists for acp ONLY** — `python -m acp` and `python -m acp.cli` work; `python -m cccp` does NOT (no cccp/__main__.py — deleted in the rename)
7. **Two annotation styles coexist** — `typing.Optional[X]` in legacy cccp interface layer vs PEP 604 (`X | None`) in acp/ and newer cccp modules (config.py, software.py, protocols.py)
8. **`CRESTInterface` has no base class** — `class CRESTInterface:` (reverse-sync restored upstream form)
9. **`# pyright:` suppressions heavy in acp/** — 18 files; 14 suppress 6+ rules each (nmr/ is the densest); pyright not in toolchain
10. **`except Exception:` bare catches** — 31 silent-swallow sites across 15 files (161 total real catches); worst: `chem/embedding.py` (6), `nmr/enumerate.py` (6), `api/v1_routes.py` (6)
11. **`HARTREE_TO_KCAL` duplicated** — `acp/core/models.py` AND `cccp/utils/constants.py` (identical value 627.5094740631)
12. **Gas-constant R duplicated 3×** — `cccp/core/candidates.py:130` (0.001987204), `cccp/core/engine.py:1726` (0.0019872041), `acp/workflows/nmr.py:379` (0.001987204259) — different precision!
13. **`conformer-search` console_script removed** — the legacy CLI (`cccp/cli.py`, `cccp/__main__.py`) was deleted; use `acp` instead. `bin/conformer-search` never existed on disk either.
14. **`XTBInterface` now standalone** — was co-located in `crest.py`, split to `qc/interfaces/xtb.py` in Phase C (2026-07-27)
15. **CI is disabled** — `.github/workflows/ci.yml` only triggers on `workflow_dispatch`; push/pull_request commented out. systemd unit `/etc/systemd/system/acp.service` is generated (not version-controlled) — edit `scripts/install_systemd.sh`, not the unit

## UNIQUE STYLES
- Module docstrings: title + `====` underline + `Author: QCcalc Team` (34 files, mostly cccp/)
- ACP backends use capability Protocols (GeometryOptimizer, SinglePointCalculator, etc.) instead of ABC — EXCEPT `QCBackend(ABC)` (backends/base.py) and `ErrorModel(ABC)` (nmr/error_model.py)
- `CRESTInterface` has no base class (`class CRESTInterface:`); `XTBInterface` standalone in `xtb.py`
- Type annotation style split: legacy cccp uses `typing.X`; acp + newer cccp use `X | None` with `from __future__ import annotations` (87/87 files)
- `logger = logging.getLogger(__name__)` in every module (62 files); pathlib.Path only — zero `os.path.*` usage; `os.replace` for atomic writes
- Scheduler launches jobs as `python -m acp.cli run <workflow>` subprocesses (undeclared but production-critical entry form)

## COMMANDS
```bash
# Install
pip install -e .
pip install -e '.[dev]'          # adds pytest + pytest-cov
pip install -e '.[api]'          # adds fastapi + uvicorn
pip install -e '.[remote]'       # adds paramiko (SSH/SFTP)

# Run (new ACP entry — recommended)
# CREST → CENSO ensemble (conformer search capability now lives in ensemble/energy via CrestBackend.search())
acp run ensemble --input "CCO" --output ./out
acp run ensemble --input "CCO" --preset censo-zero --output ./out

# free energy ranking (default: rank1-only fine DFT + ensemble total G;
# use --full-ensemble for the cumulative-Boltzmann ≥99% set, v15 semantics)
acp run energy --input "CCO" --output ./out
acp run energy --input "CCO" --full-ensemble --output ./out
acp run energy --input "CCO" --no-opt --output ./out
acp run energy --input "CCO" --levels '{"opt":{"method":"wB97X-D4","basis":"def2-SVP"},"sp":{"method":"wB97X-D4","basis":"def2-TZVPPD"}}'

# xTB-MD conformer-search free energy (GFN-FF MD → GFN1 batch opt → isostat → CENSO → fine DFT;
# default full-ensemble mode; --ewin is the GFN1 post-optimization window, distinct from energy's
# CREST window)
acp run xtbmd_censo_energy --input "CCO" --output ./out
acp run xtbmd_censo_energy --input "CCO" --md-temp 400 --md-time 100 --md-seeds 3
acp run xtbmd_censo_energy --input "CCO" --preset censo-default
acp run xtbmd_censo_energy --input "CCO" --rank1-only --resume
acp run xtbmd_censo_energy --input "CCO" --edis 0.5 --gdis 0.25 --ewin 6.0
acp run xtbmd_censo_energy --input "CCO" --no-conv-check --max-frames 300 --opt-timeout 600

# NMR chemical shift prediction
acp run nmr --input "CCO" --output ./nmr_results
acp run nmr --input "CCO" --backend orca --reference "13C=185.0" "1H=31.5"

# mechanism (TS + IRC)
acp run mechanism --reactant "C=O" --product "C[O-]" --output ./mech_out

# simple ORCA workflows
acp run singlepoint --input "CCO" --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c"
acp run frequency --input molecule.xyz

# web server
acp run serve --port 8765

# Test (real-binary tests are collection-skipped; use --run-slow to include)
pytest tests/ -v
pytest tests/ --run-slow -v
pytest -m "not slow" -v
pytest tests/test_acp_workflows_energy.py -v
pytest tests/test_acp_workflows_xtbmd_censo_energy.py -v
pytest tests/test_acp_workflows_mechanism.py -v
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
