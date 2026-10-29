# io/ — Molecular Input Handling

## OVERVIEW
Format detection, input parsing, SMILES-to-3D embedding via RDKit, batch loading. Single substantial module (421 lines).

## STRUCTURE
```
io/
├── __init__.py         # Re-exports 4 symbols
└── input_handler.py    # InputFormat, MolecularInput, MolecularInputHandler, load_batch_inputs (421 lines)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Format detection | `input_handler.py` | `InputFormat` enum: SMILES, XYZ, GJF, LOG, OUT |
| SMILES → 3D | `input_handler.py` | `MolecularInputHandler.parse_smiles()` via RDKit |
| File parsing | `input_handler.py` | `parse_xyz()`, `parse_gjf()`, `parse_log()` |
| Batch loading | `input_handler.py` | `load_batch_inputs()` — one SMILES/path per line |

## CONVENTIONS
- `InputFormat` enum for dispatch
- RDKit embedding triggered only for SMILES input — XYZ/GJF bypass to CREST
- `MolecularInput` dataclass holds parsed geometry + metadata

## NOTES
- RDKit dependency: tests will fail if RDKit is not installed
