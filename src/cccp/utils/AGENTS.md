# utils/ — Shared Utilities

## OVERVIEW
File I/O, geometry operations, resource management, solvent maps, physical constants. Largest subpackage (7 files, 25 exports).

## STRUCTURE
```
utils/
├── __init__.py           # Re-exports 25 symbols
├── file_io.py            # XYZ, GJF, JSON read/write, Gaussian energy extraction (275 lines)
├── geometry_tools.py     # GeometryUtils + LogParser (RMSD, rotation, translation) (452 lines)
├── resource_utils.py     # Memory parsing, executable discovery, ResourceManager (240 lines)
├── constants.py          # HARTREE_TO_KCAL, element masses, atomic numbers (37 lines)
├── keyword_translator.py # Gaussian keyword translation (37 lines)
└── solvent_map.py        # SOLVENT_ALIASES, orca_smd_solvent (49 lines)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| XYZ/GJF I/O | `file_io.py` | `read_xyz`, `write_xyz`, `read_gjf` |
| Geometry ops | `geometry_tools.py` | RMSD, rotation, translation, log parsing |
| Executable resolution | `resource_utils.py` | `find_executable`, `ResourceManager` |
| Unit constants | `constants.py` | `HARTREE_TO_KCAL`, `BOHR_TO_ANGSTROM`, etc. |
| Gaussian keywords | `keyword_translator.py` | Gaussian-specific but lives in utils/ |
| Solvent mapping | `solvent_map.py` | `orca_smd_solvent`, `xtb_solvent` |

## CONVENTIONS
- Comprehensive `__all__` (25 symbols) — add new exports here
- `pathlib.Path` preferred over `os.path`

## ANTI-PATTERNS
- `keyword_translator.py` is Gaussian-specific — consider moving to `qc/interfaces/`
- `GeometryUtils` and `LogParser` co-located (452 lines) — could be split into separate files
