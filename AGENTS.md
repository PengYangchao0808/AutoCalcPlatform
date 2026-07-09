# Auto-Calc Platform (ACP) — Project Knowledge Base

**Generated:** 2026-07-09
**Branch:** N/A (not a git repo)

## OVERVIEW
Automated computational chemistry platform. Two-package architecture under `src/`: legacy `conformer_search` (Phase 0) and new `acp` module (Phase 1) with stage-based workflow pipeline. CREST → DFT (Gaussian 16 / ORCA) → single-point → Shermo thermo. Python 3.10+, setuptools, YAML config.

## STRUCTURE
```
ACP_V1_20260519/
├── src/conformer_search/  # Legacy package (CLI entry + engine + QC interfaces)
│   ├── cli.py             # argparse CLI entry point (372 lines)
│   ├── config.py          # 6-source YAML config load/merge (427 lines)
│   ├── version.py         # Duplicate __version__
│   ├── core/              # ConformerEngine, ProtocolSpec, CandidateSet, state, funnel
│   ├── qc/interfaces/     # Gaussian/ORCA/CREST/XTB subprocess wrappers
│   ├── qc/runners/        # ISOSTAT clustering, Shermo thermo (DEPRECATED → use acp.backends)
│   ├── qc/cluster/        # LSF/Local cluster adapters (SLURM/PBS stubs)
│   ├── io/                # MolecularInputHandler — format detection, RDKit embedding
│   ├── pipeline/          # PipelineExecutor (anemic, 78 lines)
│   └── utils/             # File I/O, geometry, constants, solvent maps (7 files)
├── src/acp/               # NEW unified module (34 files, ~6.2k lines)
│   ├── cli.py             # argparse subcommand CLI: `acp run conformer|nmr|serve` (998 lines)
│   ├── core/              # Shared mechanism: Structure, WorkflowRunner, Registry, State
│   ├── backends/          # QC backends with capability Protocols (Gaussian/ORCA/CREST/xTB/Isostat/Molclus)
│   ├── chem/              # Chemistry-specific logic (RDKit embedding, enumeration) (404 lines)
│   ├── intake/            # Data ingestion: models, parsers, storage (610 lines)
│   ├── io/                # StructureReader / StructureWriter (thin conformer_search wrapper)
│   ├── workflows/         # Stage-based conformer search + NMR + benchmark + mechanism (4 files)
│   ├── nmr/               # Phase 3 NMR: shielding parsing, Boltzmann averaging, calibration
│   ├── reports/           # NMR report serialization (JSON/XLSX)
│   ├── api/               # Phase 2 FastAPI — server.py/routes.py/v1_routes.py/v1_schemas.py (REAL, ~1600 lines)
│   └── scheduler/         # Phase 2 task scheduler — jobs, manager, runner, store, provenance (13 files, ~2700 lines)
├── frontend/                  # ACP Workbench single-page dashboard (ACP_Workbench.html, dark theme)
├── scripts/run_g16_worker.sh  # Gaussian 16 job wrapper (scratch, disk checks, cleanup)
├── bin/conformer-search       # Legacy CLI wrapper (deprecated)
├── config/defaults.yaml       # Default YAML config (may diverge from Python built-in)
├── tests/                     # 29 test files (ACP + legacy), conftest.py, baseline configs
└── pyproject.toml             # NOTE: api optional deps (fastapi/uvicorn) NOT yet declared
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| ACP CLI (recommended) | `src/acp/cli.py` | `acp run conformer\|nmr\|serve` subcommand dispatch (~998 lines). `serve` is REAL (calls uvicorn), `mechanism` is now implemented |
| Legacy CLI | `src/conformer_search/cli.py` | `conformer-search` flat argparse |
| Add new protocol | `src/conformer_search/core/protocols.py` | Update `_get_default_protocol_config()` |
| Config loading | `src/conformer_search/config.py` | 6-source merge (see NOTES) |
| ACP backends | `src/acp/backends/` | Protocol-based capability system |
| Gaussian backend | `src/acp/backends/gaussian.py` | New ACP adapter |
| Gaussian legacy | `src/conformer_search/qc/interfaces/gaussian.py` | Subprocess via `run_g16_worker.sh` |
| CREST / xTB legacy | `src/conformer_search/qc/interfaces/crest.py` | CRESTInterface + XTBInterface co-located |
| ACP workflow stages | `src/acp/workflows/conformer.py` | 7 stage functions + 5 protocol specs |
| Core data models | `src/acp/core/models.py` | Structure, StructureRecord, StructureEnsemble |
| Workflow engine | `src/acp/core/workflow.py` | WorkflowRunner, WorkflowSpec, Stage |
| Clustering | `src/conformer_search/qc/runners/__init__.py` | `run_isostat()` (DEPRECATED) |
| Input parsing | `src/conformer_search/io/input_handler.py` | SMILES→RDKit embed; XYZ/GJF/LOG/OUT parse |
| Constants / units | `src/conformer_search/utils/constants.py` | HARTREE_TO_KCAL, element masses |
| NMR models | `src/acp/nmr/models.py` | NMRAtomShielding, NMRReport, Boltzmann averaging |
| NMR parsing | `src/acp/nmr/parser.py` | Gaussian GIAO log parser |
| NMR reports | `src/acp/reports/nmr_report.py` | JSON/XLSX serialization |
| ACP API server | `src/acp/api/server.py` | FastAPI app factory + static frontend hosting at `/` |
| API routes | `src/acp/api/routes.py` | `/api/status`, `/api/backends` implemented; `/api/jobs` + SSE NOT yet |
| API v1 routes | `src/acp/api/v1_routes.py` | Job submission, molecule upload, task management endpoints (~700 lines) |
| API schemas | `src/acp/api/schemas.py` / v1_schemas.py | Pydantic models for status, backends, jobs |
| ACP Workbench frontend | `frontend/ACP_Workbench.html` | Single-page dark dashboard (164 lines); polls `/api/status`,`/api/backends`; job submit not wired (no scheduler yet) |
| Task scheduler | `src/acp/scheduler/` | 13 files: jobs, manager, runner, store, provenance, artifacts, migrations |
| Job manager | `src/acp/scheduler/manager.py` | Job lifecycle management, polling, cancellation |
| Job runner | `src/acp/scheduler/runner.py` | Background process execution (593 lines) |
| Provenance tracking | `src/acp/scheduler/provenance.py` | Event sourcing, audit logging |
| RDKit embedding | `src/acp/chem/embedding.py` | SMILES→RDKit embed, charge assignment, enumeration (404 lines) |
| Data intake | `src/acp/intake/` | Ingestion models, file parsers, result storage |
| Mechanism workflow | `src/acp/workflows/mechanism.py` | TS search + IRC validation (now implemented) |
| CENSO recipe adapters | `src/conformer_search/recipes/` | adapter.py + censo_parts.py — protocol funnel stage mapping |

## CONVENTIONS
- **Docstrings**: Google-style (`Args:`, `Returns:`, `Attributes:`) throughout
- **Type annotations**: Universal; legacy `conformer_search/` uses `typing.Optional[X]`, new `acp/` uses `X | None` with `from __future__ import annotations`
- **Imports**: stdlib → third-party (numpy, rdkit) → local (`from conformer_search...` or `from acp...`)
- **Logging**: `logger = logging.getLogger(__name__)` in every module
- **Dataclasses**: `@dataclass(frozen=True)` preferred for specs; mutable for data containers
- **ABC**: `conformer_search/` uses `ABC` base; `acp/` uses structural `Protocol` (PEP 544)
- **Paths**: `pathlib.Path` preferred over `os.path`
- **Linter/formatter configured**: ruff (E/F/I/N/W/UP), ruff-format, mypy (strict), pre-commit with ruff hooks
- **`__all__`**: All subpackage `__init__.py` files re-export public symbols

## ANTI-PATTERNS (THIS PROJECT)
1. **NEVER import `pymatgen`** — was listed on old deps but unused; now removed from pyproject.toml
2. **NEVER update `__version__` in one place** — 3 sources: `__init__.py`, `version.py`, `pyproject.toml`
3. **NEVER change YAML defaults without updating Python built-in** — `config/defaults.yaml` vs `_get_default_config()` diverged historically
4. **NEVER add protocol to YAML `protocols` section** — unreachable; edit `_get_default_protocol_config()` in `protocols.py`
5. **NEVER bypass `scripts/run_g16_worker.sh`** — provides scratch isolation, disk checks, cleanup
6. **NEVER put implementation in `__init__.py`** — remaining: `qc/cluster/__init__.py` has `create_cluster_adapter()` factory
7. **`__main__.py` exists for both packages** — both `python -m conformer_search` and `python -m acp` work (old docs claimed no-op/missing)
8. **Two annotation styles coexist** — typing import style in conformer_search vs PEP 604 in acp
9. **CRESTInterface now inherits `QCInterfaceBase`** — fixed in Phase 1 (old docs claimed `object` inheritance)
10. **`# pyright:` suppressions heavy in acp/ modules** — 14 files suppress 6+ type-checking rules each; pyright not in project toolchain
11. **`except Exception:` bare catches** — 15 instances across 8 files silently swallow errors
12. **Never modify `HARTREE_TO_KCAL` in one place** — defined in `acp/core/models.py` AND `conformer_search/utils/constants.py`

## UNIQUE STYLES
- Module docstrings: title + `====` underline + optional `Author: QCcalc Team`
- ACP backends use capability Protocols (GeometryOptimizer, SinglePointCalculator, etc.) instead of ABC
- `CRESTInterface` now inherits from `QCInterfaceBase` (was `object`, fixed in Phase 1 refactoring)
- `bin/conformer-search` uses `sys.path.insert(0, ...)` hack — deprecated since v1.0.0
- Type annotation style split: `conformer_search/` uses `typing.X`, `acp/` uses `X | None`

## COMMANDS
```bash
# Install
pip install -e .
pip install -e '.[dev]'          # adds pytest + pytest-cov

# Run (new ACP entry — recommended)
acp run conformer --input "CCO" --output ./result
acp run conformer --input molecule.xyz --protocol ext
acp run conformer --batch-file molecules.txt --output ./batch_results

# Run (legacy entry — still works)
conformer-search --input "CCO" --output ./out
conformer-search --batch-file molecules.txt --output ./batch_out

# Test
pytest tests/ -v
pytest tests/test_acp_workflows_conformer.py -v
pytest tests/test_config.py -v
```

## NOTES
- **Config merge order** (from code):
  1. Python built-in `_get_default_config()` (authoritative)
  2. `--config` file → 3. `~/.conformer_search.yaml` → 4. `./conformer_search.yaml`
  5. `CONFSEARCH_*` env vars → 6. CLI params (`--nproc`, `--mem`)
- **ext ≈ benchmark**: Both call `_run_ext_protocol()`. Differ only in SP functional (wB97X-D4 vs DLPNO-CCSD(T))
- **RDKit embedding**: SMILES only. XYZ/GJF/LOG/OUT skip straight to CREST
- **State persistence**: `conformer_state.json` per molecule tracks stage completion
- **No CI, no git**: Project is not a git repo. Everything is manual
- **Test coverage**: 218 passed, 5 skipped (223 total). 29 test files including ACP unit tests, recipe tests. conftest.py with shared fixtures.
- **Phase 1 refactoring**: `qc/runners/__init__.py` cleaned from 277→18 lines; `qc/cluster/__init__.py` from 474→51 lines
- **`templates/` directory**: Empty legacy placeholder
- **External binaries**: Gaussian 16, ORCA, CREST, xTB, ISOSTAT, Shermo — separately installed
- **ACP IO is thin wrapper**: `src/acp/io/structures.py` delegates parsing to `conformer_search.io.input_handler`
- **Phase 2 partially started**: `src/acp/api/` has a working FastAPI skeleton (`/api/status`, `/api/backends`, static frontend at `/`); `acp run serve` calls uvicorn for real. Missing: `api` optional deps in pyproject, scheduler package, `/api/jobs`, SSE. See `docs/ACP_Frontend_Target_Implementation_Plan.md`
- **Phase 2 scheduler is real**: `src/acp/scheduler/` has 13 files (~2700 lines) — jobs, manager, runner, store, provenance, artifacts, migrations. Manager/runner are functional.
- **Phase 4 mechanism implemented**: `src/acp/workflows/mechanism.py` has TS search + IRC validation.
- **Future phases**: Phase 2 (API/FastAPI — IN PROGRESS), Phase 3 (NMR — backend done), Phase 4 (mechanism/TS — done).
