# cccp/ — Package Root

## OVERVIEW
Top-level package files: config loading, version management, package init. Hub connecting 7 subpackages (core, io, pipeline, qc, utils, backends, calculation). Library-only — no CLI (deleted in the cccp rename; use the unified `acp` CLI). Version `0.1.3` (synced 4-way: here `__init__.py`/`version.py` + `acp/__init__.py` + `pyproject.toml`, see root AGENTS.md ANTI #2).

## STRUCTURE
```
src/cccp/   # QC 底座（four-layer ①qc → ②backends → ③calculation；④acp 在外层）
├── config.py        # 6-source YAML config load/merge (+ remote-cluster sections)
├── software.py      # Centralized QC executable resolution (resolve_executable / detect_version / discover_all)
├── __init__.py      # Package init; __version__ = "0.1.3"
├── version.py       # __version__ = "0.1.3"
├── core/            # protocols, candidates, state_manager, registry (engine removed in wave-8)
├── io/              # MolecularInputHandler — format detection, RDKit embed
├── pipeline/        # PipelineExecutor — thin orchestration
├── qc/              # QC subprocess layer: interfaces/ (ORCA/CREST/xTB/CENSO/ISOSTAT/Molclus), runners/, cluster/ + shared science adapters at qc/ top level (hessian_policy, method_meta, resolved_spec, shermo_adapter, thermo_normalize, translation, keyword_registry)
├── backends/        # Capability-Protocol adapter layer (todo 12 moved here from acp.backends; no subprocess) — see backends/AGENTS.md
├── calculation/     # Task layer — the SINGLE execution station: tasks/ (14 task cores), requests/results contracts, _common, batch, selection, progress (see root AGENTS.md ANTI #17)
└── utils/           # 5 shared utility modules (constants, file_io, geometry_tools, resource_utils, solvent_map)
```

**Note:** ACP-only subpackages from the previous fork (`benchmark/`, `ensemble/`, `recipes/`, `funnel/`, `search/`, `thermo/`) and files (`core/specs.py`, `core/spec_adapter.py`, `core/method_resolution.py`, `qc/interfaces/xtb.py`, `qc/runners/isostat.py`, `qc/runners/shermo.py`) were **removed** during the 2026-07-13 reverse-sync. ACP features they backed (NMR config, nmr_shielding) were re-merged into the authoritative base. `qc/interfaces/xtb.py` was **re-created in Phase C (2026-07-27)** when `XTBInterface` was split out of `crest.py`; `qc/runners/*` and `qc/cluster/*` remain consolidated in their `__init__.py`. On **2026-08-02** the CENSO/ISOSTAT/Molclus subprocess logic was consolidated **into** `qc/interfaces/censo.py`, `isostat.py`, `molclus.py` (single subprocess layer); `run_isostat` was removed in wave-8 (IsostatInterface is the single ISOSTAT path).

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Config loading | `config.py` | 6-source merge see root AGENTS.md for order |
| **Executable resolution** | **`software.py`** | **THE single resolver — `resolve_executable(name, configured_path)`; all backends/interfaces/API/catalog route through it. `resource_utils.find_executable` is superseded (unwired legacy)** |
| Version | `__init__.py` + `version.py` (+ `acp/__init__.py` + `pyproject.toml`) | **4-way sync** — see root ANTI #2 |
| Task cores (execution) | `calculation/tasks/` | Single execution station; contracts via `calculation/{requests,results}.py` |
| Capability backends | `backends/` | Implementation home; `acp/backends/*` are pure re-export shims |
| Subpackage docs | `core/AGENTS.md`, `utils/AGENTS.md`, `backends/AGENTS.md`, etc. |

## CONVENTIONS
- **Imports**: Full package path — `(engine.py removed in wave-8)`
- **`__all__`**: All subpackage `__init__.py` files re-export public symbols
- **Config defaults**: Python built-in `_get_default_config()` is authoritative, NOT `config/defaults.yaml`
- **`__version__`**: 4-way duplication (`cccp/__init__.py`, `cccp/version.py`, `acp/__init__.py`, `pyproject.toml`) — must update ALL
- **Dependency direction**: `src/cccp/**` must never import `acp` (incl. lazy/TYPE_CHECKING); backends must not import task modules (root ANTI #17)

## ANTI-PATTERNS
- **Version duplication**: `__version__` lives in four files across the two packages — diverges easily
- **No CLI / no `__main__.py`**: the legacy CLI (`cli.py`, `__main__.py`) and `conformer-search` console_script were deleted in the cccp rename; `python -m cccp` does NOT work — use the unified `acp` CLI instead
