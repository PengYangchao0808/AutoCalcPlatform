# acp/workflows/ — Workflow Modules

## OVERVIEW
Stage-based workflow implementations for the active ACP workflows. 4 user-facing workflows — ensemble, energy, mechanism, simple — plus a registry that maps CLI subcommands to `WorkflowSpec` builders. 7 files, ~2800 lines. The retired conformer/nmr/benchmark workflows were removed in 2026-07-27 (catalog entries kept as `status:"retired"` for historical-job display only).

## STRUCTURE
```
workflows/
├── __init__.py      # PEP 562 lazy re-exports (import acp.workflows is cheap/side-effect free)
├── _helpers.py      # Small shared helpers
├── registry.py      # CLI subcommand → WorkflowSpec builder mapping (SUPPORTED_WORKFLOWS-driven)
├── ensemble.py      # `acp run ensemble` — CREST → CENSO preset+screening (499 lines)
├── energy.py        # `acp run energy` — Boltzmann ≥99%, opt/freq same-level handoff (1156 lines)
├── mechanism.py     # `acp run mechanism` — TS search + IRC validation (394 lines)
└── simple.py        # `acp run singlepoint|opt|freq|optfreq|optfreqsp|scan|xtb-opt` (571 lines)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Ensemble entry | `ensemble.py` | `run_ensemble_generation()` — CREST conformer search → CENSO refine |
| Energy entry | `energy.py` | `run_conformer_energy()` — CENSO screening → cumulative-Boltzmann selection → DFT handoff (opt/freq same-level) + Shermo |
| CREST call | `ensemble.py` / `energy.py` | Go through `get_backend("crest")(...).search(...)` (Phase C wiring; do NOT bypass the backend layer with `CRESTInterface` directly) |
| Rank1 DFT handoff | `energy.py` | `_run_rank1_handoff()` — opt → sp → freq → Shermo on one conformer |
| Conformer candidate dict | `energy.py` | `_censo_record_to_candidate()` — cheap --no-opt path; `source` preserves the CENSO `conf_id` (e.g. `CONF1`) |
| Boltzmann selection | `energy.py` | `_select_cumulative_boltzmann()` / `_boltzmann_weights()` |
| Mechanism TS | `mechanism.py` | `run_mechanism_analysis()` — TS search + IRC validation + energy barrier |
| Mechanism energy | `mechanism.py` | `_compute_energy_barrier()` — barrier in kcal/mol |
| Simple workflows | `simple.py` | `run_singlepoint` / `run_optimize` / `run_frequency` / `run_optfreq` / `run_optfreqsp` — single-structure ORCA tasks |
| CLI → workflow mapping | `registry.py` | `list_workflow_entries()` / `get_workflow_entry()` — driven by `catalog.SUPPORTED_WORKFLOWS`; conformer/nmr/benchmark intentionally absent |

## CONVENTIONS
- **Lazy loading**: `__init__.py` uses PEP 562 `__getattr__` + `_LAZY_SOURCES` so importing one workflow does not pull in the others (keeps third-party deps decoupled).
- **Backend layer**: QC execution goes through `acp.backends` adapters (`get_backend(...)`), never the raw `cccp.qc.interfaces` classes, from the workflow layer.
- **Config**: `cccp.config.load_config()` resolves the merged config; workflows receive it as a dict.
- **Retired workflows**: `conformer` / `nmr` / `benchmark` were removed; their `catalog.WORKFLOW_CATALOG` entries remain with `status:"retired"` + `visible:False` so historical jobs still render. Do not re-add registry entries for them.

## ANTI-PATTERNS
- **energy.py size**: 1156 lines — the handoff + boltzmann + writers + entry point all co-located; candidate for future split.
- **`# pyright:` suppressions**: a few type-checking rules suppressed; pyright is not in the project toolchain.
- **`CrestBackend.optimize()` / a few Protocol methods raise `NotImplementedError`** but structurally satisfy their capability Protocol (`isinstance(...)` is True) — capability declaration vs actual usability mismatch (pre-existing, not introduced by the workflow retire).
