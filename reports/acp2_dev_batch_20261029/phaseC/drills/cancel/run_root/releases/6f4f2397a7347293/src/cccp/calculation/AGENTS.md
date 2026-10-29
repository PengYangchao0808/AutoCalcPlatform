# calculation/ — Task Layer: the SINGLE Execution Station (ACP 2.0)

## OVERVIEW
**50 .py.** The only layer that executes QC work: 15 task cores in `tasks/` (each exactly one `run_*`), the `TaskRequest`/`TaskResult` contracts, batch execution, and — new in ACP 2.0 — the capability registry / task binding (`capabilities/`), versioned science products (`products/`), and the science recipe compiler (`recipes/`). All 15 workflows reach QC exclusively through here (root AGENTS.md ANTI #17); `acp.calculations.primitives` is a pure forwarder + ACP publish contracts; `acp_services.runtime.step_dag` injects science from here.

## STRUCTURE
```
calculation/
├── tasks/             # 15 cores (16 files; __init__ = re-export shell): singlepoint, optimize, frequency, scan, irc,
│                      # casscf, thermochemistry, conformer_search, md_sampling, clustering, censo_refine,
│                      # nmr_shielding, xtb_path_search, orca_gradient, xtb_constrained_opt — one run_* each
├── requests.py        # 2049 L: TaskRequest, MethodSpec, all *Options, P2_TASK_CONTRACTS; TaskKind = 15 members
├── results.py         # 1596 L: TaskResult, ErrorKind, 15 payload types, FrequencyAnalysis
├── contracts.py       # 1123 L: ElectronicStateSpec, CASSCFSpec, SpinMode, GuessSpec, WavefunctionPolicy, SpinQualityGate
├── _common.py         # electronic-state gating: CalculationInputs, apply_stability_options, assess_state_quality, build_state_scf_options, write_state_artifacts
├── batch.py           # run_batch + CacheStore/FileSystemCacheStore
├── context.py         # TaskContext, resolve_context
├── selection.py       # two-step select_semantic / precheck_runtime
├── progress.py        # ProgressEvent / ProgressEventKind / TaskProgressSink
├── errors.py          # 6 neutral task errors
├── frequency_parse.py # ORCA frequency science (used by analysis/orca_parser)
├── energy_adapters.py / xtbmd_adapters.py
├── optimization_trajectory.py / irc_trajectory.py / irc_endpoints.py   # trajectory recorders
├── capabilities/      # ACP2 NEW (6): contracts.py (907 L — CapabilityDescriptor, CapabilityProjections ×4, ExecutionSemantics
│                      #   compute vs pure_analysis, TASK_KINDS), registry.py (read-only deterministic CapabilityRegistry),
│                      #   builtin.py (electronic.energy→singlepoint, geometry.optimize→optimize, vibration.compute→frequency,
│                      #   vibration.analyze→build_frequency_report; resolve_analysis_entrypoint; UNSUPPORTED_FAMILIES),
│                      #   domains.py (nmr.shielding, nmr.compare, thermochemistry.ensemble — pure_analysis, frozen snapshot),
│                      #   task_binding.py (1208 L — bind_capability/bind_task_kind/build_task_request/preflight_binding/
│                      #   execute_binding, TaskEntrypoint, TASK_ENTRYPOINTS)
├── products/          # ACP2 NEW (8): contracts.py (1459 L — ScienceProduct, ProductKind =
│                      #   structure_trajectory|curve_scatter|vibration|derived, ProductIdentity, compute_input_digest,
│                      #   point_stable_id), registry.py, serialize.py (migrate_v0_to_v1), builders structure/curve/vibration/derived
└── recipes/           # ACP2 NEW (4) — "science recipes": contracts.py (1138 L — Recipe, Step, OutputRef, Selector,
                       #   Alternative, Limits, ValidatorRef, existing_task_kinds() = 15-kind inventory),
                       #   validation.py (validate_recipe, blocked_steps, RecipeCycleError, MissingRequiredStepError,
                       #   DiagnosticDependencyError), compile.py (1432 L — compile_recipe → deterministic TaskRequest DAG,
                       #   CompiledRecipe/CompiledStep, RecipeInputs, StepExecutionIdentity, materialize_step_identity,
                       #   read_historical_recipe)
```

## WHERE TO LOOK
| Concern | Location |
|---|---|
| Run one task | `tasks/<name>.py::run_*` (the ONLY execution bodies) |
| Build a request | `requests.py` (+ `capabilities/task_binding.py::build_task_request` when capability-driven) |
| Electronic state / CASSCF options | `contracts.py` + `_common.py` gating |
| Multi-item batch | `batch.py` (ACP `BatchOptimizeEngine` delegates here) |
| Publish science products | `products/` (consumed acp-side via `results/frames_adapter.py`) |
| Compile a recipe to a DAG | `recipes/compile.py`; validate = `recipes/validation.py` |
| Capability admission/binding | `capabilities/{registry,builtin,domains,task_binding}.py` |

## CONVENTIONS
- **Single execution station**: any new computing capability lands here first (root ANTI #17); backends never import task modules
- **One task core = one `run_*`**; contracts flow through `requests.py`/`results.py` only
- **Deterministic compiles**: `compile_recipe` output depends only on inputs; `StepExecutionIdentity` is materialized, not guessed
- **Products are versioned**: `ScienceProduct` v1 + `migrate_v0_to_v1`; `point_stable_id` must be stable across re-runs
- **Capability snapshots are frozen**: `domains.py` keeps built-in snapshot/digest frozen — extend additively

## ANTI-PATTERNS
- **Stale docstring**: `requests.py` says "seven P2 capability tasks" but `TaskKind` has 15 members (7 core + 8 P2 incl. `xtb_constrained_opt`) — fix the docstring, never the enum count
- **Second `run_*` implementation** outside `tasks/` (acp side fails `test_unique_primitive_definitions`)
- **Recipe cycles / diagnostic deps as normal results**: `RecipeCycleError` / `DiagnosticDependencyError` guard the DAG (Execution Protocol #4)

## GUARD TESTS
`tests/test_cccp_calculation_contracts.py` · `tests/test_acp2_science_task_binding.py` (capability↔15 cores) · `tests/test_acp2_science_recipe.py` + `_da_recipe.py` (DAG invariants) · `tests/test_acp2_science_registry.py` / `_schema.py` / `_inventory.py` · `tests/test_cccp_science_products.py` + `_extended.py` + `tests/test_cccp_bounded_products.py` (round-trip / no fabrication)
