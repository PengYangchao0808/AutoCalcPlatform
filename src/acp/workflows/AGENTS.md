# acp/workflows/ — Workflow Modules

## OVERVIEW
Stage-based workflow implementations for the active ACP workflows. 5 user-facing workflows — ensemble, energy, mechanism, nmr (GIAO + DP4/DP5, reactivated 2026-08-07), simple — plus a registry that maps CLI subcommands to `WorkflowSpec` builders, and the xtbmd_censo_energy pipeline stages (Phases 2–4 of docs/ACP_xTBMD_CENSO_Energy_DevDoc.html). The `acp run mechanism` entry now delegates directly into `acp/mechanism/study_runner.py` (study-only path), so there is no local `workflows/mechanism.py`. The retired conformer/benchmark workflows were removed in 2026-07-27 (nmr was removed then but revived in P1a; catalog entries for conformer/benchmark kept as `status:"retired"` for historical-job display only).

## STRUCTURE
```
workflows/
├── __init__.py              # PEP 562 lazy re-exports (import acp.workflows is cheap/side-effect free)
├── _helpers.py              # Small shared helpers
├── registry.py              # CLI subcommand → WorkflowSpec builder mapping (SUPPORTED_WORKFLOWS-driven)
├── ensemble.py              # `acp run ensemble` — CREST → CENSO preset+screening (398 lines)
├── ensemble_thermo.py       # Ensemble total-Gibbs helpers (mixing_entropy / ensemble_total_gibbs[_from_values] / EnsembleThermoSummary)
├── energy.py                # `acp run energy` — Boltzmann ≥99%, opt/freq same-level handoff (739 lines; heavy helpers live in energy_shared.py)
├── energy_shared.py         # Shared energy helpers (E4 extraction: resolve_levels / run_rank1_handoff / boltzmann_weights / select_cumulative_boltzmann / build_ensemble_summary / write_final_outputs / censo_record_to_candidate / build_result_ensemble / xtb_passthrough_result / resolve_solvent_config / resolve_crest_ewin)
├── nmr.py                   # `acp run nmr` — conformer search → GIAO → Boltzmann averaging → DP4/DP5
├── simple.py                # `acp run singlepoint|opt|freq|scan|irc|xtb-opt` (447 lines)
├── xtbmd_md.py              # Multi-replica xTB-MD sampling convention (run_md_replicas, Phase 2)
└── xtbmd_censo_energy.py    # xtbmd_censo_energy pipeline (2080 lines): `_batch_opt_frames` GFN1 batch optimization (Phase 3) + `run_xtbmd_censo_energy` orchestration (Phase 4)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Ensemble entry | `ensemble.py` | `run_ensemble_generation()` — CREST conformer search → CENSO refine |
| Energy entry | `energy.py` | `run_conformer_energy()` — CENSO screening → cumulative-Boltzmann selection → DFT handoff (opt/freq same-level) + Shermo |
| CREST call | `ensemble.py` / `energy.py` | Go through `get_backend("crest")(...).search(...)` (Phase C wiring; do NOT bypass the backend layer with `CRESTInterface` directly) |
| ORCA handoff | `energy_shared.py` | `run_rank1_handoff` builds the ORCA backend via `get_backend("orca")(cfg, method=..., basis=..., solvent=..., solvent_model=...)` (since 2026-08-02 consolidation; previously a direct `ORCAInterface` construction) — do NOT regress to raw cccp imports |
| Shermo thermo | `energy_shared.py` | Shermo imported via cccp.qc.runners (cccp direct, todo-12 permitted) |
| Shared energy helpers | `energy_shared.py` | Public extraction (E4) — `resolve_levels` / `run_rank1_handoff` (opt→freq→SP→Shermo) / `boltzmann_weights` / `select_cumulative_boltzmann` / `build_ensemble_summary` / `write_final_outputs` (categorized `RESULT/` products + ensemble_thermo.json + TOTAL row + boltzmann_table.json; legacy `finalDFT/` is read-only) / `censo_record_to_candidate` (cheap --no-opt path; `source` preserves the CENSO `conf_id`) / `build_result_ensemble` / `xtb_passthrough_result` / `resolve_solvent_config` / `resolve_crest_ewin`. Shared by `energy.py` / `ensemble.py` (private-name aliases) and `xtbmd_censo_energy.py` (public names). Import from here, never from `energy.py`/`ensemble.py` private names |
| xTB-MD CENSO energy entry | `xtbmd_censo_energy.py` | `run_xtbmd_censo_energy()` — Phase 4 orchestration: embed → run_md_replicas → `_batch_opt_frames` → ISOSTAT → `_filter_energy_window` (GFN1 ewin, sidecar primary) → CENSO 3 presets × dual modes → `write_final_outputs`; per-stage checkpoint fingerprints for `--resume`; empty-ensemble fail-fast |
| Mechanism study entry | `../mechanism/study_runner.py` | `run_mechanism_study()` / `resume_mechanism_study()` — S0→S4 network study |
| Simple workflows | `simple.py` | `run_singlepoint` / `run_optimize` / `run_frequency` / `run_scan` / `run_irc` / `run_xtb_optimize` — single-request tasks |
| CLI → workflow mapping | `registry.py` | `list_workflow_entries()` / `get_workflow_entry()` — driven by `catalog.SUPPORTED_WORKFLOWS`; conformer/nmr/benchmark intentionally absent |
| Multi-replica MD sampling | `xtbmd_md.py` | `run_md_replicas()` — seed increments + distinct RDKit multi-start conformations (md_seeds > 1) + trajectory merge; single-trajectory responsibility lives in `MolclusBackend.run_md` |
| GFN1 batch optimization | `xtbmd_censo_energy.py` | `_batch_opt_frames()` — Phase 3: per-replica ±2σ equilibration discard, max_frames uniform subsampling, per-frame nproc=1 ThreadPool, per-frame timeout, `isomers_xyz` + `isomers_energies.json` sidecar, fail-fast, geometric pre-check + ISOSTAT-based conv-check diagnostics; returns `BatchOptResult` |
| Energy-window filter | `xtbmd_censo_energy.py` | `_filter_energy_window()` — Phase 4: sidecar energies (primary, geometric match via vectorized plain-RMSD + Kabsch fallback) + title compat channel; rewrites ensemble_xyz titles (first float = GFN1 Hartree, parsed by `xtb_passthrough_result`) + `ensemble_energies.json` |

## CONVENTIONS
- **Lazy loading**: `__init__.py` uses PEP 562 `__getattr__` + `_LAZY_SOURCES` so importing one workflow does not pull in the others (keeps third-party deps decoupled).
- **Backend layer**: QC execution goes through `acp.backends` adapters (`get_backend(...)`), never the raw `cccp.qc.interfaces` classes, from the workflow layer.
- **Config**: `cccp.config.load_config()` resolves the merged config; workflows receive it as a dict.
- **Retired workflows**: `conformer` / `benchmark` were removed; their `catalog.WORKFLOW_CATALOG` entries remain with `status:"retired"` + `visible:False` so historical jobs still render. Do not re-add registry entries for them. **NMR was reactivated 2026-08-07 (P1a)** and now has a full `acp/workflows/nmr.py` + `acp/nmr/` package (see `docs/ACP_NMR_DP4_DevDoc.md`).
- **xtbmd_censo_energy pipeline**: `xtbmd_md.py` / `xtbmd_censo_energy.py` implement the workflow-layer conventions of docs/ACP_xTBMD_CENSO_Energy_DevDoc.html (Phases 1–5 done: CLI subcommand, catalog three entries + FIELD_DEFINITIONS 20 fields, runner/script_gen whitelist via shared `xtbmd_method_flags()` in `scheduler/jobs.py` — booleans normalised via `_as_bool`, `md_timeout` forwarded, `--ewin` CLI default None so config stays reachable — stage_tasks plan provider with censo-default forced-opt semantics, frontend submit branch; Phase 6 smoke on the compute node pending). Energy-sidecar JSON is the canonical GFN1 energy channel; XYZ title energies are the compatibility channel. **ISOSTAT only accepts Molclus bare-energy titles** (`        -11.39433937`) — `Frame N | Energy: X` titles make it exit 24; `IsostatInterface.cluster()` (via the `isostat` backend) normalises titles internally, so workflow writers must not assume ISOSTAT tolerates their format. opt_level is validated against the xTB set (crude/normal/tight/verytight) — "loose" is not a legal xTB level; the catalog FIELD_DEFINITIONS xtb per_backend list reflects the same set, and option membership compares str() so numeric API values pass. `--resume` fingerprints are per-stage checkpoints (`xtbmd`/`batch_opt`/`isostat`), covering each stage's full input parameter set; a fingerprint mismatch raises (refuse stale reuse) — see §17/§18/§18.1 of the devdoc for the audit notes.

## ANTI-PATTERNS
- **energy.py historically 1156 lines** — the E4 extraction (energy_shared.py) moved the handoff + boltzmann + writers + resolvers out; energy.py is now 739 lines. Keep shared helpers in `energy_shared.py`, not in energy.py.
- **`# pyright:` suppressions**: a few type-checking rules suppressed; pyright is not in the project toolchain.
- **`CrestBackend.optimize()` / a few Protocol methods raise `NotImplementedError`** but structurally satisfy their capability Protocol (`isinstance(...)` is True) — capability declaration vs actual usability mismatch (pre-existing, not introduced by the workflow retire).
