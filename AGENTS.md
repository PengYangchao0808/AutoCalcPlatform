# Auto-Calc Platform (ACP) — Project Knowledge Base

**Generated:** 2026-07-09
**Branch:** N/A (not a git repo)

## OVERVIEW
Automated computational chemistry platform. Two-package architecture under `src/`: legacy `conformer_search` (Phase 0) and new `acp` module (Phase 1) with stage-based workflow pipeline. CREST → DFT (Gaussian 16 / ORCA) → single-point → Shermo thermo. Python 3.10+, setuptools, YAML config.

## STRUCTURE
```
ACP_V1_20260519/
├── src/conformer_search/  # Authoritative conformer-search package (30 .py; reverse-synced 2026-07-13 from compute node)
│   ├── cli.py             # argparse CLI entry point
│   ├── config.py          # 6-source YAML config load/merge (+ NMR + remote-cluster sections)
│   ├── version.py         # __version__
│   ├── core/              # ConformerEngine (1733 lines), ProtocolSpec, CandidateSet, state_manager
│   ├── qc/interfaces/     # Gaussian/ORCA/CREST(+XTBInterface co-located)/xtb_thermo subprocess wrappers
│   ├── qc/runners/        # run_isostat / run_shermo / batch_process_thermo (all in __init__.py)
│   ├── qc/cluster/        # Local + LSF adapters + factory (single __init__.py)
│   ├── io/                # MolecularInputHandler — format detection, RDKit embedding
│   ├── pipeline/          # PipelineExecutor (thin)
│   └── utils/             # File I/O, geometry, constants, solvent maps
├── src/acp/               # NEW unified module (34 files, ~6.2k lines)
│   ├── cli.py             # argparse subcommand CLI: `acp run conformer|nmr|serve` (998 lines)
│   ├── core/              # Shared mechanism: Structure, WorkflowRunner, Registry, State
│   ├── backends/          # QC backends with capability Protocols (Gaussian/ORCA/CREST/xTB/Isostat/Molclus)
│   ├── chem/              # Chemistry-specific logic (RDKit embedding, enumeration) (404 lines)
│   ├── intake/            # Data ingestion: models, parsers, storage (610 lines)
│   ├── io/                # StructureReader / StructureWriter (thin conformer_search wrapper)
│   ├── workflows/         # Stage-based conformer search + NMR + benchmark + mechanism (4 files)
│   ├── nmr/               # Phase 3 NMR: ORCA shielding parsing, Boltzmann averaging, calibration
│   ├── reports/           # NMR report serialization (JSON/XLSX)
│   ├── api/               # Phase 2 FastAPI — server.py/routes.py/v1_routes.py/v1_schemas.py (REAL, ~1600 lines)
│   └── scheduler/         # Phase 2 task scheduler — jobs, manager, runner, store, provenance (13 files, ~2700 lines)
├── frontend/                  # ACP Workbench single-page dashboard (ACP_Workbench.html, dark theme)
├── scripts/start_acp.sh  # ACP service wrapper (scratch, disk checks, cleanup)
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
| ORCA backend | `src/acp/backends/orca.py` | New ACP adapter |
| Gaussian legacy | `src/conformer_search/qc/interfaces/orca.py` | Subprocess via direct ORCA invocation |
| CREST / xTB legacy | `src/conformer_search/qc/interfaces/crest.py` | CRESTInterface + XTBInterface co-located |
| ACP conformer workflow | `src/acp/workflows/conformer.py` | Thin wrapper delegating to authoritative `ConformerEngine.run()`; rebuilds ensemble from `all_conformers.xyz` |
| CENSO backend | `src/acp/backends/censo_backend.py` | Subprocess wrapper: presets, rcfile gen, JSON/XYZ parsing, template injection (per-run HOME), keep_all. Copies input into censo/ (CENSO chdirs to input's parent) |
| Ensemble workflow | `src/acp/workflows/ensemble.py` | `acp run ensemble` — CREST → CENSO P+S (censo-light/default); censo-zero = CREST xTB passthrough (no CENSO) |
| Energy workflow | `src/acp/workflows/energy.py` | `acp run energy` — cumulative-Boltzmann ≥99% ensemble (v15 semantics, `censo.refinement_threshold`); full `--levels` field consumption → ORCA route_extras; opt/freq same-level rule (v7) |
| CENSO dev doc | `docs/ACP_CENSO_Integration_DevDoc.html` | Authoritative design + P1–P5 audit history (v14: acceptance passed) |
| Core data models | `src/acp/core/models.py` | Structure, StructureRecord, StructureEnsemble |
| Workflow engine | `src/acp/core/workflow.py` | WorkflowRunner, WorkflowSpec, Stage |
| Clustering | `src/conformer_search/qc/runners/__init__.py` | `run_isostat()` (DEPRECATED) |
| Input parsing | `src/conformer_search/io/input_handler.py` | SMILES→RDKit embed; XYZ/GJF/LOG/OUT parse |
| Constants / units | `src/conformer_search/utils/constants.py` | HARTREE_TO_KCAL, element masses |
| NMR models | `src/acp/nmr/models.py` | NMRAtomShielding, NMRReport, Boltzmann averaging |
| NMR parsing | `src/acp/nmr/parser.py` | ORCA Gaussian GIAO log parser legacy Gaussian GIAO log parser |
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
5. **
6. **NEVER put implementation in `__init__.py`** — remaining: `qc/cluster/__init__.py` has `create_cluster_adapter()` factory
7. **`__main__.py` exists for both packages** — both `python -m conformer_search` and `python -m acp` work (old docs claimed no-op/missing)
8. **Two annotation styles coexist** — typing import style in conformer_search vs PEP 604 in acp
9. **`CRESTInterface` has no base class in the authoritative version** — `class CRESTInterface:` (the ACP fork had made it inherit `QCInterfaceBase`; reverse-sync restored the upstream form)
10. **`# pyright:` suppressions heavy in acp/ modules** — 14 files suppress 6+ type-checking rules each; pyright not in project toolchain
11. **`except Exception:` bare catches** — 15 instances across 8 files silently swallow errors
12. **Never modify `HARTREE_TO_KCAL` in one place** — defined in `acp/core/models.py` AND `conformer_search/utils/constants.py`

## UNIQUE STYLES
- Module docstrings: title + `====` underline + optional `Author: QCcalc Team`
- ACP backends use capability Protocols (GeometryOptimizer, SinglePointCalculator, etc.) instead of ABC
- `CRESTInterface` has no base class in the authoritative version (`class CRESTInterface:`); `XTBInterface` is co-located in `crest.py`
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
acp run ensemble --input "CCO" --output ./out           # CREST → CENSO ensemble (censo-light)
acp run energy --input "CCO" --output ./out             # rank1 free energy (opt+freq+SP+Shermo)
acp run energy --input "CCO" --no-opt                   # cheap RSH//xTB path (CENSO refinement)

# Run (legacy entry — still works)
conformer-search --input "CCO" --output ./out
conformer-search --batch-file molecules.txt --output ./batch_out

# Web dashboard (systemd service)
sudo systemctl restart acp          # Reload after code changes
sudo systemctl start acp            # Start
sudo systemctl stop acp             # Stop
sudo journalctl -u acp -f          # Tail logs

# Test
pytest tests/ -v
pytest tests/test_acp_workflows_conformer.py -v
pytest tests/test_config.py -v
```

## SYSTEMD SERVICE
- **Service name**: `acp.service`
- **Config file**: `/etc/systemd/system/acp.service`
- **Runs as**: user `<user>`
- **URL**: http://localhost:8765
- **Logs**: `sudo journalctl -u acp -f`
- **Reload reminder**: After any code modification to `src/acp/api/` (or any source file), you **MUST** run `sudo systemctl restart acp` for the changes to take effect. The service does **not** use `--reload`, so a manual restart is required.
