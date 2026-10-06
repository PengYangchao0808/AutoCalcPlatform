# nmr/ — NMR + DP4/DP5 Stereochemistry (Phase 3)

## OVERVIEW
Goodman DP4/DP5 NMR chemical-shift prediction and stereochemistry assignment on top of the ACP conformer-search + ORCA GIAO pipeline (reactivated 2026-08-07 / P1a; see `docs/ACP_NMR_DP4_DevDoc.md`). 25 modules + 1 data dir, ~19k lines (as of 2026-10-06, after the `acp-nmr-goodman-gap-remediation` pass). Orchestrated by `acp/workflows/nmr.py`; this package owns all NMR-domain logic: shielding models, Boltzmann averaging, DP4/DP5 probability with typed states, FCHL kernels, four-layer spectra model + per-nucleus processors, assignment (two-phase + iterative), evidence gates, protocol spec / reference validation, atomic diagnostics, analysis revision, report serialization, and the isolated `ShiftPredictor`/DP5q research seam.

## STRUCTURE
```
nmr/
├── __init__.py                 # 100+ re-exported symbols grouped by module
├── models.py                   # NmrConfig, ExperimentalNmr/Peak, ConformerShielding, SignalGroup, Assignment, AtomShift, CandidateResult, RegressionResult, NmrReport, ProcessingAssessment/DigitalFilterCheck, REPORT_SCHEMA_VERSION=2, lookup_tms_shieldings (2197 L)
├── structure_map.py            # NmrStructureMap / AtomIdentity — stable atom_uid + label schemes + input→candidate→conformer→QC mapping (G03; todo 2, 505 L)
├── io.py                       # parse_experimental_nmr — per-peak assignment state + ambiguity label sets + parse_errors (326 L)
├── equivalence.py              # detect_equivalence_groups (no topology ⇒ single-atom groups + equivalence_unknown; never element-merge, 289 L)
├── averaging.py                # boltzmann_average_shieldings — emits SignalGroup per signal (289 L)
├── enumerate.py                # P2 candidate enumeration: EnumerateOptions, enumerate_candidates, enumerate_to_smiles (536 L)
├── assignment.py               # two-phase match (locked pairs + Hungarian) → AssignmentResult / unmatched diagnostics (436 L)
├── iterative_assignment.py     # G11 iterative assignment: capacity-aware Hungarian ⟷ internal scaling to a fixed point (todo 46, 1343 L)
├── scaling.py                  # fit_scaling_goodman + split R² (r2_regression / r2_prediction), scaled-exp residual convention (296 L)
├── probability.py              # compute_dp4 / normalize_dp4 (gated) / compute_dp5 / stable dp5_log_to_probability (271 L)
├── error_model.py              # ErrorModel(ABC), GoodmanErrorModel (stable log-CDF), GoodmanDP5Model (+FCHL weighted KDE, support/OOD, Dp5ProbabilityRecord) (958 L)
├── fchl.py                     # FCHL19 representation + kernels (qml / pure-numpy), fragment path + typed availability/support (fragment_path_status) (1526 L)
├── spectra.py                  # four-layer spectrum pipeline: acquisition/processing/lines/resonances, FFT/phase/baseline/quality gates, multi-experiment select, 2D `ser` rejection, processor registry wiring (1324 L)
├── carbon_processor.py         # ¹³C processor: peak fitting + solvent exclusion + resonance grouping (todo 43, 1768 L)
├── proton_processor.py         # ¹H processor: BIC multiplet grouping + total-H/methyl constraints + overlap uncertainty (todo 44, 1499 L)
├── spectra_registry.py         # element→processor registry (C/H), descriptor/typed lookup (todo 45, 284 L)
├── method_config.py            # resolve_nmr_method — catalog method → typed effective config → local/remote argv (G06, 671 L)
├── protocol.py                 # NmrProtocolSpec (6 segments) + fingerprint + exploratory/reference_validation/acp_calibrated aggregation (todo 29, 346 L)
├── reference_validation.py     # reference segment gate + compare_reference_vs_migration (typed unavailable; NOT a calibration, todo 31, 701 L)
├── atomic_diagnostics.py       # per-atom/signal risk records, per-nucleus DP4 split, leave-one-signal-out, conflict matrix (G16, todo 39, 825 L)
├── analysis_revision.py        # AnalysisRevision / PeakEdit — recompute analysis only (no QC) after peak edits (G10/G15, todo 47, 744 L)
├── shift_predictor.py          # ShiftPredictor Protocol + isolated registry + provenance (G17; never replaces GIAO/DP4/DP5, todo 55, 557 L)
├── dp5q_stub.py                # isolated DP5q stub adapter (loadable, not trained; never default) (G17, todo 56, 652 L)
├── report.py                   # write_json_report (schema v2 + processing_quality) / write_xlsx_report / write_plots / write_all_reports (356 L)
└── models/                     # ⚠ DATA DIR (NOT a Python package — no __init__.py): DP5 ML artifacts — atomic_reps.gz (22MB), frag_reps.gz (18MB), i_w_kde_mean_s_0.025.p (24MB), c_w_kde_mean_s_0.025.p, folded_scaled_errors.p, tms_references.txt, LICENSE-DP5, NOTICE.md
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Data models | `models.py` | NmrConfig drives the whole workflow; NmrReport is the serialization container; SignalGroup is the shared signal definition (average→assignment→DP5) |
| Stable atom mapping | `structure_map.py` | `atom_uid` / `source_atom_index` / two label schemes; EQ/OMIT/stereocenters resolve through it |
| TMS reference shifts | `models.py` | `lookup_tms_shieldings` — nuclei table |
| Experimental input | `io.py` | `parse_experimental_nmr` — per-peak assignment state, `H32 or H33` ambiguity sets, `parse_errors` |
| Equivalence groups | `equivalence.py` | Topology-based symmetry; without a mol → single-atom groups + `equivalence_unknown` |
| Boltzmann averaging | `averaging.py` | Weighted mean shielding; SignalGroup emission with coefficients and basis |
| Candidate enumeration | `enumerate.py` | Diastereomer/stereoisomer enumeration (P2) |
| Atom↔peak assignment | `assignment.py` + `scaling.py` + `iterative_assignment.py` | two-phase match → regression fit → iterative assignment with capacity/overlap constraints |
| DP4 probability | `probability.py` | `compute_dp4` (nucleus-aggregated); gated normalization excludes invalid/insufficient candidates |
| DP5 probability | `probability.py` + `error_model.py` | typed `Dp5ProbabilityRecord`; unavailable ⇒ probability null; out-of-domain support warns, never silent |
| FCHL kernels | `fchl.py` | Optional (qml extra or `ACP_FCHL_NUMPY=1`); <86 atoms = atomic_reps, ≥86 = radius-3 fragments gated by `fragment_path_status()` (typed available/openbabel-missing/assets-missing/residual-index-mismatch; shipped frag assets fail the residual-index pairing) |
| Raw spectra (Bruker) | `spectra.py` + `carbon_processor.py` + `proton_processor.py` + `spectra_registry.py` | Four-layer model, processing provenance/quality gates, per-nucleus processors, deterministic experiment selection, 2D `ser` rejection |
| Method resolution | `method_config.py` | `resolve_nmr_method` — single source for GUI/API/CLI/local/remote argv (G06) |
| Protocol spec / reference gate | `protocol.py` + `reference_validation.py` | Six-segment fingerprint + calibration modes; reference gate is typed, NOT a calibration claim |
| Diagnostics | `atomic_diagnostics.py` | Atomic risk (`diagnostics` namespace) vs calibrated probability (`probability` namespace); leave-one-signal-out + conflict matrix |
| Peak-edit recompute | `analysis_revision.py` | `revise_nmr_analysis` (workflows/nmr.py) — no QC rerun; WAITING_REVIEW review-only semantics |
| DP5q research seam | `shift_predictor.py` + `dp5q_stub.py` | Isolated registry; never selected by default, never feeds the Goodman error model |
| Report emission | `report.py` | schema v2 nmr_report.json + nmr_assignment.xlsx + scatter/error PNGs (RESULT/reports/plots/) |
| Workflow entry | `src/acp/workflows/nmr.py` | `run_nmr_analysis` + `revise_nmr_analysis` (3892 L) |
| Evaluation harness | `tests/benchmark/nmr/` + `scripts/nmr_benchmark.py` | Dataset manifests, layered metrics, pre-registered thresholds, A/B protocol transfer, reference validation, ShiftPredictor screening (harness-level only; not accuracy claims) |
| Dev doc | `docs/ACP_NMR_DP4_DevDoc.md` | Authoritative design + P1–P4 audit history + §11.2 corrections + D.9 acceptance status |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` (matches `acp/` style)
- **Pyright suppressions**: nearly every module has a heavy `# pyright:` header (6–9 rules suppressed); pyright is not in the toolchain
- **Optional deps**: openpyxl (XLSX), matplotlib (plots), qml (FCHL), nmrglue (Bruker), openbabel (fragments) — all degrade gracefully with typed status, never hard-require
- **Typed probability semantics**: missing / not-applicable DP5 ⇒ `probability=None` (never 0); placeholder path only under explicit `error_model=placeholder-*` and writes the separate `dp5_diagnostic_score`
- **DP5 model binding**: `load_dp5_model` requires `models/` artifacts present; guard with `dp5_model_available()` before calling DP5 functions
- **ShiftPredictor isolation**: `shift_predictor.py` / `dp5q_stub.py` must never be imported by `src/acp/workflows/**`, `error_model.py`, or `probability.py` (test-guarded); predictor residuals never enter the Goodman Gaussian/KDE error model
- **`__all__`**: `__init__.py` re-exports 100+ symbols grouped by module

## ANTI-PATTERNS
- **`models/` dir masquerades as a package**: no `__init__.py` — it is DP5 binary data (~65MB) checked into the source tree. Do NOT import it as `acp.nmr.models.*` submodule; name collides with sibling `models.py`
- **Bare `except Exception:`**: historical sites in `fchl.py` / `enumerate.py` / `spectra.py` / `error_model.py` — do not add new ones (root ANTI #8)
- **Heavy pyright suppression**: all modules disable `reportUnknown*` etc. — type-checking debt concentrated here (P1a fast-track)
- **qml fragility**: `qml` only builds against numpy<2 — the pure-numpy kernel is a real opt-in fallback (`ACP_FCHL_NUMPY=1`); never claim the compiled kernel was verified when it was not
- **Do not silently drop evidence**: no element-merge fallback, no null→0, no placeholder-as-formal probability, no spectrum peak silently discarded — typed reasons only (`equivalence_unknown`, `parse_errors`, `unavailable`, `out_of_domain`, …)
