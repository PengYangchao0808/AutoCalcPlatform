# acp/intake/ — Data Ingestion

## OVERVIEW
Parses molecular input files (XYZ/SDF/MOL/GJF/ORCA INP/SMILES) into typed data models. 4 files, ~610 lines.

## STRUCTURE
```
intake/
├── __init__.py   # Re-exports: StructureAsset, StructureParseResult, 8 parse functions
├── models.py     # StructureAsset (22 fields), StructureParseResult — dataclass domain models
├── parsers.py    # detect_format() + 6 parse_*_text() functions + 3 internal helpers (482 lines)
└── storage.py    # UploadStorage — file I/O for uploads/normalized copies (61 lines)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Parsing entry | `parsers.py` | `parse_structure_text(content, fmt)` — dispatch to format-specific parser |
| Format detection | `parsers.py:detect_format` | Extension-based + content heuristics; returns "xyz"|"sdf"|"mol"|"gjf"|"inp"|"smiles" |
| XYZ parsing | `parsers.py:parse_xyz_text` | Multi-frame XYZ, charge/multi from comment |
| SDF parsing | `parsers.py:parse_sdf_text` | Uses RDKit `Chem.MolFromMolBlock`; falls back to manual parser |
| MOL parsing | `parsers.py:parse_mol_text` | Single MOL block, same RDKit/fallback dual path |
| GJF parsing | `parsers.py:parse_gjf_text` | Route section → blank → charge/mult → coords |
| ORCA INP parsing | `parsers.py:parse_orca_inp_text` | `*xyz` block extraction, charge/mult keywords |
| SMILES parsing | `parsers.py:parse_smiles_list` | RDKit ETKDG embedding; lines starting with `#` are comments |
| Upload flow | `storage.py:UploadStorage` | `save_upload()` (bytes→disk, 50MB cap) + `save_normalized()` |
| Data model | `models.py` | `StructureAsset` (per-molecule) + `StructureParseResult` (batch container) |

## ANTI-PATTERNS
- **bare `except Exception:`** at `parsers.py:243` — `rdMolDescriptors.CalcMolFormula` failure caught silently, falls back to Hill formula
- **Global mutable counter** `_ASSET_COUNTER` in `parsers.py` — not thread-safe; `_reset_asset_counter()` exists but nowhere called (dead code)
- **`Path_safe` helper** at `parsers.py:345` — PascalCase name (reads like a class); used as str sanitizer for filenames
- **No upload cleanup** — `UploadStorage` creates directories but has no `remove_upload()` / TTL eviction
- **RDKit import inside functions** — `_parse_molblock` and `parse_smiles_list` both import lazily; `parse_smiles_list` bails with error if RDKit missing, `_parse_molblock` falls back to manual parser
