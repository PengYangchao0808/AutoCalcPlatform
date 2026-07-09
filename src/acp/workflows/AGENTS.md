# acp/workflows/ — Workflow Modules

## OVERVIEW
Stage-based workflow implementations. 4 workflows: conformer search (7 stage functions, 5 protocol variants), NMR prediction, benchmark, and mechanism (TS + IRC). 5 files, ~1200 lines total.

## STRUCTURE
```
workflows/
├── __init__.py      # Re-exports run_conformer_search, run_nmr_calculation, run_benchmark
├── conformer.py     # 7 stage functions + get_protocol_stages() + run_conformer_search() (465 lines)
├── nmr.py           # run_nmr_calculation() + stage_nmr_build_report() + ORCA rejection
├── benchmark.py     # BenchmarkRunner + run_benchmark() (multi-protocol comparison)
└── mechanism.py     # TS search + IRC validation (now implemented)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Conformer entry | `conformer.py` | `run_conformer_search()` — main entry point called by `acp cli` |
| Protocol resolution | `conformer.py` | `get_protocol_stages()` maps protocol name to stage list |
| Stage: embed | `conformer.py` | `stage_embed_smiles()` — RDKit embedding or file copy |
| Stage: CREST | `conformer.py` | `stage_crest_search()` — GFN0→GFN2 two-stage or single |
| Stage: cluster | `conformer.py` | `stage_isostat_cluster()` — ISOSTAT → split ensemble |
| Stage: DFT opt | `conformer.py` | `stage_dft_optimize()` — full/shared handoff via legacy engine |
| Stage: SP | `conformer.py` | `stage_single_point()` — zero-protocol SP pipeline |
| Stage: frequency | `conformer.py` | `stage_frequency()` — structural no-op (freq inside handoff) |
| Stage: thermo | `conformer.py` | `stage_shermo_thermo()` — structural no-op (thermo inside handoff) |
| Boltzmann weighting | `conformer.py` | `boltzmann_weight_ensemble()` — free energy based |
| Finalization | `conformer.py` | `_finalize_conformer_results()` — delegates to legacy engine |
| NMR workflow | `nmr.py` | `run_nmr_calculation()` — dispatches to Gaussian; ORCA raises NotImplementedError |
| Benchmark | `benchmark.py` | `run_benchmark()` — runs multiple protocols for comparison |
| Mechanism TS | `mechanism.py` | TS search + IRC validation + energy barrier calculation |
| Mechanism energy | `mechanism.py` | `_compute_energy_barrier()` — barrier in kcal/mol |

## CONVENTIONS
- **Stage function signature**: `(ctx: WorkflowContext, data: StructureEnsemble, **params) -> StructureEnsemble`
- **Pipeline orchestration**: `WorkflowRunner` executes stages sequentially from `WorkflowSpec`
- **5 protocols**: `ext` (default), `full`, `lite`, `zero`, `benchmark` — each produces different stage lists
- **Legacy delegation**: Core computation delegated to `conformer_search.core.engine.ConformerEngine`
- **Cached engine**: `_ensure_engine()` creates/caches `ConformerEngine` in `ctx.backends` dict

## ANTI-PATTERNS
- **Structural no-op stages**: `stage_frequency` and `stage_shermo_thermo` are pass-throughs — actual work happens inside the DFT handoff. Added for future decoupling but currently misleading.
- **Heavy legacy coupling**: ConformerEngine internals accessed via underscore methods (`_step_crest_search`, `_run_shared_dft_handoff`, `_finalize_results`)
- **Engine caching in backends dict**: `_ensure_engine()` stores engine in `ctx.backends[_ENGINE_KEY]` — semantic misuse of "backends" namespace
- **Protocol logic split**: Protocol-to-stage mapping in `conformer.py` duplicates logic from `conformer_search.core.protocols.resolve_protocol_spec()`