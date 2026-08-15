# nmr/ — NMR + DP4/DP5 Stereochemistry (Phase 3)

## OVERVIEW
Goodman DP4/DP5 NMR chemical-shift prediction and stereochemistry assignment on top of the ACP conformer-search + ORCA GIAO pipeline (reactivated 2026-08-07 / P1a; see `docs/ACP_NMR_DP4_DevDoc.md`). 13 modules + 1 data dir, ~4155 lines. Orchestrated by `acp/workflows/nmr.py`; this package owns all NMR-domain logic (shielding models, Boltzmann averaging, DP4/DP5 probability, FCHL kernels, Bruker spectra, report serialization).

## STRUCTURE
```
nmr/
├── __init__.py          # 60+ re-exported symbols (models, io, equivalence, averaging, enumerate, assignment, scaling, probability, error_model, FCHL, spectra)
├── models.py            # NmrConfig, ExperimentalNmr/Peak, ConformerShielding, Assignment, AtomShift, CandidateResult, RegressionResult, NmrReport, lookup_tms_shieldings (385 L)
├── io.py                # parse_experimental_nmr — experimental shifts from text (140 L)
├── equivalence.py       # build_all_labels / detect_equivalence_groups / merge_explicit_and_detected (194 L)
├── averaging.py         # boltzmann_average_shieldings (177 L)
├── enumerate.py         # P2 candidate enumeration: EnumerateOptions, enumerate_candidates, enumerate_to_smiles (541 L)
├── assignment.py        # match_assigned / match_unassigned / collect_residual_inputs (172 L)
├── scaling.py           # fit_regression / fit_scaling_goodman / build_assignments (182 L)
├── probability.py       # compute_dp4 / normalize_dp4 / compute_dp5 / compute_dp5_goodman / dp5_log_to_probability (139 L)
├── error_model.py       # ErrorModel(ABC), GoodmanErrorModel, GoodmanDP5Model, load_error_model/load_dp5_model (533 L)
├── fchl.py              # P4 FCHL atomic representations: build_atom_representations, load_atomic_reps, get_atomic_kernels_numpy (801 L)
├── spectra.py           # P3 Bruker experiment processing: find/process_bruker_experiment, ProcessedSpectrum (565 L)
├── report.py            # write_json_report / write_xlsx_report / write_plots / write_all_reports (175 L)
└── models/              # ⚠ DATA DIR (NOT a Python package — no __init__.py): DP5 ML artifacts — atomic_reps.gz (22MB), frag_reps.gz (18MB), i_w_kde_mean_s_0.025.p (24MB), c_w_kde_mean_s_0.025.p, folded_scaled_errors.p, tms_references.txt, LICENSE-DP5, NOTICE.md
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Data models | `models.py` | NmrConfig drives the whole workflow; NmrReport is the serialization container |
| TMS reference shifts | `models.py` | `lookup_tms_shieldings` — nuclei table |
| Experimental input | `io.py` | `parse_experimental_nmr` — multi-format shift file parser |
| Equivalence groups | `equivalence.py` | Symmetry detection + explicit label merge |
| Boltzmann averaging | `averaging.py` | Weighted mean shielding over conformer ensemble |
| Candidate enumeration | `enumerate.py` | Diastereomer/stereoisomer enumeration (P2) |
| Atom↔peak assignment | `assignment.py` + `scaling.py` | match → regression fit → build_assignments |
| DP4 probability | `probability.py` | `compute_dp4` (nucleus-aggregated) |
| DP5 probability | `probability.py` + `error_model.py` | `compute_dp5`/`compute_dp5_goodman` need error model from `load_dp5_model` |
| FCHL kernels | `fchl.py` | Optional (qml extra); gated by `fchl_assets_available()` / `dp5_fchl_available()` |
| Bruker data | `spectra.py` | P3: process Bruker experiment trees (needs `acp.nmr.models` conventions) |
| Report emission | `report.py` | nmr_report.json + nmr_assignment.xlsx + scatter/error PNGs |
| Workflow entry | `src/acp/workflows/nmr.py` | Conformer search → GIAO → Boltzmann averaging → DP4/DP5 (502 L) |
| Dev doc | `docs/ACP_NMR_DP4_DevDoc.md` | Authoritative design + P1–P4 audit history |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` (matches `acp/` style)
- **Pyright suppressions**: nearly every module has a heavy `# pyright:` header (6–9 rules suppressed); pyright is not in the toolchain
- **Optional deps**: openpyxl (XLSX), matplotlib (plots), qml (FCHL/DP5) — all degrade gracefully with `try/except ImportError` + warning, never hard-require
- **DP5 model binding**: `load_dp5_model` requires `models/` artifacts present; guard with `dp5_model_available()` before calling DP5 functions
- **`__all__`**: `__init__.py` re-exports 60+ symbols grouped by module

## ANTI-PATTERNS
- **`models/` dir masquerades as a package**: no `__init__.py` — it is DP5 binary data (~64MB) checked into the source tree. Do NOT import it as `acp.nmr.models.*` submodule; name collides with sibling `models.py`
- **Bare `except Exception:`**: 8+ sites swallow silently — `fchl.py:173` (return False), `enumerate.py:221/225/242/274/426/454` (return None/mol/[]/pass), `spectra.py` and `error_model.py` fallbacks
- **Heavy pyright suppression**: all modules disable `reportUnknown*` etc. — type-checking debt concentrated here (P1a fast-track)
- **qml fragility**: `qml` only builds against numpy<2 (per pyproject comment) — FCHL/DP5 paths are environment-fragile by design
