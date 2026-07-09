# conformer_search/ — Package Root

## OVERVIEW
Top-level package files: CLI entry point, config loading, version management, package init. Hub connecting 5 subpackages (core, io, pipeline, qc, utils).

## STRUCTURE
```
src/conformer_search/
├── cli.py           # argparse CLI, main() entry point (372 lines)
├── config.py        # 6-source YAML config load/merge (427 lines)
├── __init__.py      # Package init; re-exports; __version__ = "1.0.0"
├── __main__.py      # Works — calls conformer_search.cli.main()
├── version.py       # Duplicate __version__ = "1.0.0"
├── core/            # ConformerEngine, protocols, candidates, state, funnel
├── io/              # MolecularInputHandler — format detection, RDKit embed
├── pipeline/        # PipelineExecutor — thin orchestration (78 lines)
├── qc/              # QC interfaces, runners, cluster adapters
├── benchmark/       # Benchmark runner (128 lines)
├── ensemble/        # CandidateSet (Boltzmann, RMSD, sorting) (325 lines)
├── recipes/         # CENSO adapter + protocol parts (606 lines)
├── utils/           # 7 shared utility modules (file I/O, geometry, constants)
├── funnel/          # Empty (placeholder)
├── search/          # Empty (placeholder)
└── thermo/          # Empty (placeholder)
```

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
