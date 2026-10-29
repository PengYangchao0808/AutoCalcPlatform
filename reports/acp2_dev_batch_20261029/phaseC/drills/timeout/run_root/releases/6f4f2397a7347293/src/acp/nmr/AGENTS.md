# nmr/ — NMR + DP4/DP5 Stereochemistry (Phase 3, forwarder surface)

## OVERVIEW
Goodman DP4/DP5 NMR chemical-shift prediction and stereochemistry assignment on top of the ACP conformer-search + ORCA GIAO pipeline (reactivated 2026-08-07 / P1a; see `docs/reference/current/ACP_NMR_DP4_DevDoc.md`). **Since the ACP 2.0 C05 split the science lives in `src/cccp/analysis/nmr/` (27 `.py`, ~20.3k lines + `model_assets/`); this package is a compatibility forwarder surface plus the small ACP-side revision/method-config half.** 25 `.py`, ~1513 lines total (2026-10-26): 22 modules are pure re-export shims or `sys.modules[__name__] = _impl` aliases, and only `analysis_revision.py` (463 L) / `method_config.py` (46 L) keep real acp-side code. Orchestrated by `acp/workflows/nmr.py` (4029 L); every `acp.nmr.*` import resolves to the identical `cccp.analysis.nmr.*` object (root ANTI #30 identity forwarder + #34 NMR single implementation root — never a second implementation).

## STRUCTURE
```
nmr/
├── __init__.py                 # 338 L — additive re-export only: 140 __all__ symbols assembled from the shim modules (no implementation; root ANTI #5)
├── analysis_revision.py        # 463 L — ONLY real platform logic: PeakRevision / AnalysisRevision / NmrAnalysisSnapshot, revision_identity, analysis_result_identity, review_only_status → JobStatus.WAITING_REVIEW, write/load_revision_record, load_revision_index, utc_now_iso; imports the pure PeakEdit/digest half from cccp.analysis.nmr.analysis_revision
├── method_config.py            # 46 L — platform half of resolve_nmr_method: injects acp.catalog.METHOD_META / FIELD_DEFINITIONS into cccp.analysis.nmr.method_config.resolve_nmr_method (CLI/scheduler signature unchanged)
├── assignment.py               # 16 L — sys.modules[__name__] = _impl alias
├── carbon_processor.py         # 16 L — sys.modules alias
├── dp5q_stub.py                # 17 L — sys.modules alias (isolated seam; importing registers nothing)
├── error_model.py              # 16 L — sys.modules alias
├── fchl.py                     # 17 L — sys.modules alias
├── iterative_assignment.py     # 16 L — sys.modules alias
├── probability.py              # 16 L — sys.modules alias
├── proton_processor.py         # 16 L — sys.modules alias
├── scaling.py                  # 16 L — sys.modules alias
├── spectra.py                  # 16 L — sys.modules alias
├── spectra_registry.py         # 16 L — sys.modules alias
├── atomic_diagnostics.py       # 42 L — explicit re-export shim → cccp.analysis.nmr.atomic_diagnostics
├── averaging.py                # 20 L — re-export shim
├── enumerate.py                # 28 L — re-export shim
├── equivalence.py              # 37 L — re-export shim
├── io.py                       # 16 L — re-export shim
├── models.py                   # 131 L — re-export shim → cccp.analysis.nmr.models (NmrConfig, NmrReport, SignalGroup, lookup_tms_shieldings, …)
├── protocol.py                 # 42 L — re-export shim
├── reference_validation.py     # 46 L — re-export shim
├── report.py                   # 47 L — re-export shim (write_json_report / write_xlsx_report / write_plots / write_all_reports)
├── shift_predictor.py          # 51 L — re-export shim (isolated research seam)
├── structure_map.py            # 28 L — re-export shim
└── models/                     # ⚠ EMPTY leftover directory (no __init__.py, 0 files) — the DP5/FCHL artifacts moved to cccp/analysis/nmr/model_assets/; the single locator is cccp/analysis/nmr/assets.py. Do not import it as a package; do not recreate assets here; do not delete for line count (root ANTI #31)
```

## WHERE TO LOOK
| Task | Implementation (`cccp.analysis.nmr.*`) | `acp.nmr` surface / Notes |
|------|----------------------------------------|--------------------------|
| Data models | `models.py` (2201 L) | `models.py` 131 L re-export; NmrConfig drives the whole workflow, NmrReport is the serialization container, SignalGroup is the shared signal definition (average→assignment→DP5) |
| Stable atom mapping | `structure_map.py` (505 L) | `structure_map.py` re-export; `atom_uid` / `source_atom_index` / two label schemes; EQ/OMIT/stereocenters resolve through it |
| TMS reference shifts | `models.py::lookup_tms_shieldings` | via `acp.nmr.models`; table loaded through the asset locator |
| Experimental input | `io.py` (326 L) | `io.py` re-export; `parse_experimental_nmr` — per-peak assignment state, `H32 or H33` ambiguity sets, `parse_errors` |
| Equivalence groups | `equivalence.py` (289 L) | `equivalence.py` re-export; topology-based symmetry; without a mol → single-atom groups + `equivalence_unknown` |
| Boltzmann averaging | `averaging.py` (289 L) | `averaging.py` re-export; weighted mean shielding + SignalGroup emission with coefficients and basis |
| Candidate enumeration | `enumerate.py` (534 L) | `enumerate.py` re-export; diastereomer/stereoisomer enumeration (P2) |
| Atom↔peak assignment | `assignment.py` (436 L) + `scaling.py` (296 L) + `iterative_assignment.py` (1343 L) | alias / shim; two-phase match → regression fit → capacity-aware iterative assignment to a fixed point |
| DP4 probability | `probability.py` (271 L) | `probability.py` alias; `compute_dp4` (nucleus-aggregated); gated normalization excludes invalid/insufficient candidates |
| DP5 probability | `probability.py` + `error_model.py` (964 L) | alias / shim; typed `Dp5ProbabilityRecord`; unavailable ⇒ probability null; out-of-domain support warns, never silent |
| FCHL kernels | `fchl.py` (1528 L) | `fchl.py` alias; optional (qml extra or `ACP_FCHL_NUMPY=1`); <86 atoms = atomic_reps, ≥86 = radius-3 fragments gated by `fragment_path_status()` (typed available/openbabel-missing/assets-missing/residual-index-mismatch; shipped frag assets fail the residual-index pairing) |
| Raw spectra (Bruker) | `spectra.py` (1324 L) + `carbon_processor.py` (1768 L) + `proton_processor.py` (1499 L) + `spectra_registry.py` (284 L) | aliases; four-layer model, processing provenance/quality gates, per-nucleus processors, deterministic experiment selection, 2D `ser` rejection |
| Method resolution | `method_config.py` (682 L) | `acp/nmr/method_config.py` 46 L wrapper; single source for GUI/API/CLI/local/remote argv (G06) — the wrapper only supplies catalog vocabularies |
| Protocol spec / reference gate | `protocol.py` (366 L) + `reference_validation.py` (709 L) | re-export shims; six-segment fingerprint + calibration modes; reference gate is typed, NOT a calibration claim |
| Diagnostics | `atomic_diagnostics.py` (825 L) | re-export shim; atomic risk (`diagnostics` namespace) vs calibrated probability (`probability` namespace); leave-one-signal-out + conflict matrix |
| Peak-edit science | `analysis_revision.py` (365 L) — `PeakEdit`, `apply_peak_edits`, `experiment_peak_digest`, `analysis_evidence_hash`, `verify_evidence_hash` | pure science half; no QC rerun, typed `EvidenceHashMismatchError` |
| Revision record / persistence | — | `acp/nmr/analysis_revision.py` only; `revise_nmr_analysis` (`workflows/nmr.py`) recomputes analysis stages; review maps onto existing `JobStatus.WAITING_REVIEW` (no new lifecycle entry) |
| DP5q research seam | `shift_predictor.py` (557 L) + `dp5q_stub.py` (652 L) | `acp.nmr.shift_predictor` / `dp5q_stub` shims; isolated registry, loadable but never trained/default, importing/loading registers nothing, never feeds the Goodman error model |
| Report emission | `report.py` (356 L) | re-export shim; schema v2 nmr_report.json + nmr_assignment.xlsx + scatter/error PNGs (`RESULT/reports/plots/`) |
| NMR shielding comparison (A-24a) | `comparison.py` (1335 L) — `compare_nmr_shielding`, `NmrComparisonOutcome` | **no acp shim**: import `cccp.analysis.nmr.comparison` directly; pure analysis, no subprocess/task import |
| Model assets | `assets.py` (54 L) — `asset_dir()/resolve_asset()`; data dir `model_assets/` (8 files, ~63 MB) | single locator; `acp.nmr.models/error_model/fchl/reference_validation` receive paths from it — never hardcode a `model_assets` path |
| Package exports | `cccp/analysis/nmr/__init__.py` (579 L, additive) | `acp/nmr/__init__.py` 338 L re-exports 140 symbols; processor modules stay unimported so the registry stays lazy |
| Workflow entry | — | `src/acp/workflows/nmr.py` — `run_nmr_analysis` + `revise_nmr_analysis` (4029 L) |
| Evaluation harness | — | `tests/benchmark/nmr/` + `scripts/nmr_benchmark.py` — dataset manifests, layered metrics, pre-registered thresholds, A/B protocol transfer, reference validation, ShiftPredictor screening (harness-level only; not accuracy claims) |
| Tests | — | `tests/test_acp_nmr_*.py` (37 files) + `tests/test_acp2_science_nmr_binding.py`; assets/golden tests import `cccp.analysis.nmr.assets` directly |
| Dev doc | — | `docs/reference/current/ACP_NMR_DP4_DevDoc.md` — authoritative design + P1–P4 audit history + §11.2 corrections + D.9 acceptance status |

## CONVENTIONS
- **Single implementation root**: NMR/DP4/DP5/FCHL science is implemented only in `cccp/analysis/nmr/`. `acp/nmr/` forwards — adding a science body here is a regression (root ANTI #17/#30/#34)
- **Two forwarder shapes**: 11 modules are `sys.modules[__name__] = _impl` aliases (assignment, carbon_processor, dp5q_stub, error_model, fchl, iterative_assignment, probability, proton_processor, scaling, spectra, spectra_registry) so `unittest.mock.patch` / `monkeypatch.setattr` / `isinstance` identity keep working; 11 are explicit re-export shims. Edit the cccp implementation, never the shim
- **Only two acp-side modules**: `analysis_revision.py` (record + atomic platform paths + `Structure` coupling) and `method_config.py` (catalog injection). Both are platform-half splits of cccp science, not forks
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` (both packages for new code)
- **Pyright suppressions**: 22 of 27 cccp nmr modules (3–10 rules each) plus `acp/nmr/analysis_revision.py` carry `# pyright:` headers; pyright is not in the toolchain
- **Dependency split**: matplotlib + openpyxl are **core** deps (XLSX/plots must never be silently skipped in supported installs); `nmr` extra = nmrglue (Bruker), openbabel (fragments), qml (FCHL) — optional paths degrade with typed status, never hard-require
- **Typed probability semantics**: missing / not-applicable DP5 ⇒ `probability=None` (never 0); placeholder path only under explicit `error_model=placeholder-*` and writes the separate `dp5_diagnostic_score`
- **DP5 model binding**: `load_dp5_model(models_dir=None)` resolves `model_assets/` through `cccp.analysis.nmr.assets`; guard with `dp5_model_available()` before calling DP5 functions
- **ShiftPredictor isolation**: `shift_predictor` / `dp5q_stub` must never be imported by `src/acp/workflows/**`, `error_model`, or `probability` (test-guarded); predictor residuals never enter the Goodman Gaussian/KDE error model
- **`__all__`**: `acp/nmr/__init__.py` re-exports 140 symbols grouped by module; no implementation lives there

## ANTI-PATTERNS
- **Re-implementing NMR science in `acp/nmr/`**: the forwarder surface exists only for compatibility. New/changed NMR logic goes to `src/cccp/analysis/nmr/` and is re-exported from the shim — never copied or "fixed" acp-side (root ANTI #17/#30/#34)
- **Deleting forwarders for line count**: the 22 shims/aliases and the empty `models/` leftover are retained compatibility surfaces (root ANTI #31). `acp.nmr.<module>` identity is part of the contract
- **`models/` leftover is not a package**: no `__init__.py`, zero files, and the name collides with sibling `models.py`. Do not import it as `acp.nmr.models.<submodule>`, do not put assets back, do not hardcode `model_assets` paths — use `cccp.analysis.nmr.assets` (root ANTI #34)
- **Bare `except Exception:`**: historical sites in `cccp/analysis/nmr/enumerate.py` (7), `fchl.py`, `models.py`, `spectra.py` — do not add new ones (root ANTI #8)
- **Heavy pyright suppression**: concentrated in the cccp nmr modules; type-checking debt is inherited from the P1a fast-track — do not treat the suppressions as "clean"
- **qml fragility**: `qml` only builds against numpy<2 — the pure-numpy kernel is a real opt-in fallback (`ACP_FCHL_NUMPY=1`); never claim the compiled kernel was verified when it was not
- **Do not silently drop evidence**: no element-merge fallback, no null→0, no placeholder-as-formal probability, no spectrum peak silently discarded — typed reasons only (`equivalence_unknown`, `parse_errors`, `unavailable`, `out_of_domain`, …)
