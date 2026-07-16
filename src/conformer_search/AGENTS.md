# conformer_search/ — Package Root

## OVERVIEW
Top-level package files: CLI entry point, config loading, version management, package init. Hub connecting 5 subpackages (core, io, pipeline, qc, utils).

## STRUCTURE
```
src/conformer_search/   # Authoritative version (30 .py; reverse-synced 2026-07-13 from compute node)
├── cli.py           # argparse CLI, main() entry point
├── config.py        # 6-source YAML config load/merge (+ NMR + remote-cluster sections)
├── __init__.py      # Package init; __version__ = "1.0.0"
├── __main__.py      # Works — calls conformer_search.cli.main()
├── version.py       # __version__ = "1.0.0"
├── core/            # ConformerEngine (1733 lines), protocols, candidates, state_manager
├── io/              # MolecularInputHandler — format detection, RDKit embed
├── pipeline/        # PipelineExecutor — thin orchestration
├── qc/              # QC interfaces (Gaussian/ORCA/CREST+XTB/xtb_thermo), runners, cluster adapters
└── utils/           # 7 shared utility modules (file I/O, geometry, constants)
```

**Note:** ACP-only subpackages from the previous fork (`benchmark/`, `ensemble/`, `recipes/`, `funnel/`, `search/`, `thermo/`) and files (`core/specs.py`, `core/spec_adapter.py`, `core/method_resolution.py`, `qc/interfaces/xtb.py`, `qc/runners/isostat.py`, `qc/runners/shermo.py`) were **removed** during the 2026-07-13 reverse-sync. ACP features they backed (NMR config, nmr_shielding) were re-merged into the authoritative base; see root `AGENTS.md` and `docs/remote_execution_plan.html` (§conformer_search 版本分歧).

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| CLI entry point | `cli.py` | `main()` — argparse, batch/single dispatch |
| Config loading | `config.py` | 6-source merge see root AGENTS.md for order |
| Version | `__init__.py` + `version.py` | **DUPLICATED** — bump BOTH |
| `python -m conformer_search` | `__main__.py` | **Functional** — calls `conformer_search.cli:main()` |
| Subpackage docs | `core/AGENTS.md`, `utils/AGENTS.md`, etc. |

## CONVENTIONS
- **Imports**: Full package path — `from conformer_search.core.engine import ConformerEngine`
- **`__all__`**: All subpackage `__init__.py` files re-export public symbols
- **Config defaults**: Python built-in `_get_default_config()` is authoritative, NOT `config/defaults.yaml`
- **`__version__`**: Duplicated in `__init__.py` and `version.py` — must update BOTH

## ANTI-PATTERNS
- **`__main__.py` is functional**: `python -m conformer_search` calls `main()` — old docs claimed it was a no-op
- **Version duplication**: `__version__` lives in two files — diverges easily
- **CLI also has `if __name__ == '__main__': main()`**: Redundant with installed entry point
- **`bin/conformer-search` legacy wrapper**: Uses `sys.path.insert(0, ...)` hack; redundant with `pyproject.toml` entry point
