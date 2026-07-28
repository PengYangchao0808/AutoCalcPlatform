# cccp/ — Package Root

## OVERVIEW
Top-level package files: config loading, version management, package init. Hub connecting 5 subpackages (core, io, pipeline, qc, utils). Library-only — no CLI (deleted in the cccp rename; use the unified `acp` CLI).

## STRUCTURE
```
src/cccp/   # Authoritative version (reverse-synced 2026-07-13 from compute node)
├── config.py        # 6-source YAML config load/merge (+ remote-cluster sections)
├── __init__.py      # Package init; __version__ = "1.0.0"
├── version.py       # __version__ = "1.0.0"
├── core/            # ConformerEngine (dormant), protocols, candidates, state_manager
├── io/              # MolecularInputHandler — format detection, RDKit embed
├── pipeline/        # PipelineExecutor — thin orchestration
├── qc/              # QC interfaces (ORCA/CREST/XTB/xtb_thermo), runners, cluster adapters
└── utils/           # 7 shared utility modules (file I/O, geometry, constants)
```

**Note:** ACP-only subpackages from the previous fork (`benchmark/`, `ensemble/`, `recipes/`, `funnel/`, `search/`, `thermo/`) and files (`core/specs.py`, `core/spec_adapter.py`, `core/method_resolution.py`, `qc/interfaces/xtb.py`, `qc/runners/isostat.py`, `qc/runners/shermo.py`) were **removed** during the 2026-07-13 reverse-sync. ACP features they backed (NMR config, nmr_shielding) were re-merged into the authoritative base. `qc/interfaces/xtb.py` was **re-created in Phase C (2026-07-27)** when `XTBInterface` was split out of `crest.py`; `qc/runners/*` and `qc/cluster/*` remain consolidated in their `__init__.py`.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Config loading | `config.py` | 6-source merge see root AGENTS.md for order |
| Version | `__init__.py` + `version.py` | **DUPLICATED** — bump BOTH |
| Subpackage docs | `core/AGENTS.md`, `utils/AGENTS.md`, etc. |

## CONVENTIONS
- **Imports**: Full package path — `from cccp.core.engine import ConformerEngine`
- **`__all__`**: All subpackage `__init__.py` files re-export public symbols
- **Config defaults**: Python built-in `_get_default_config()` is authoritative, NOT `config/defaults.yaml`
- **`__version__`**: Duplicated in `__init__.py` and `version.py` — must update BOTH

## ANTI-PATTERNS
- **Version duplication**: `__version__` lives in two files — diverges easily
- **No CLI / no `__main__.py`**: the legacy CLI (`cli.py`, `__main__.py`) and `conformer-search` console_script were deleted in the cccp rename; `python -m cccp` does NOT work — use the unified `acp` CLI instead
