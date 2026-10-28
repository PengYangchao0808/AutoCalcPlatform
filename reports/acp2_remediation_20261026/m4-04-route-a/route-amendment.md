# M4-04 re-run — ADR-008 Amendment A record

## 1. What changed

| file | change |
| --- | --- |
| `docs/reference/next-change/adr/ADR-008-limited-diels-alder-criteria.md` | **new §18 Amendment A**: TS-guess route `GFN2-xTB path` → `PESsearch ORCA relaxed scan → explicit frame rule → BatchOptimize TS (opt_freq)`; §7/§4/§8/§9/§10/§11/§12 untouched |
| `scripts/acceptance/acp2_m404_da.py` | step 2 `candidate_path` producer replaced; `select_scan_frame()` added; route receipt added to preflight/summary/report/chain; `FrozenInputs.formed_bonds` derived from the frozen `reaction.json` |
| `tests/test_acp2_m404_da.py` | old “highest-energy PATH frame” assertions replaced by Amendment A rule tests + reference-fixture digest/consistency tests |
| `tests/fixtures/acp2/science/diels_alder/reference-case/` | new read-only reference-case fixture (17 files + provenance + README) |

No file under `src/cccp/analysis/research/` was modified. `science_criteria.py`
and its frozen tables are read-only in this change.

## 2. ADR diff summary

Added §18 (after the existing §17 sign-off):

- §18.1 **R1 scan**: ORCA relaxed scan through the real PESsearch workflow
  (`run_bond_length_scan`), coordinates = the two *formed* bonds derived from
  the frozen `reaction.json` mapped into reactant atom order, start = distance
  in the frozen product geometry, end = start + 2.0 Å, 21 points, GFN2-xTB
  optimiser, B97-3c single points, `reuse_previous_geometry=true`,
  `full_scan=true`, `max_iterations=250`.
- §18.1 **R2 frame rule**: first frame with `optimization_converged`,
  `constraint_residual_ok`, empty `invalid_reasons`, and
  `reaction_progress >= 0.5`; **no fallback**.
- §18.1 **R3 refinement**: real BatchOptimize `opt_freq` with `TAG: TS`,
  `r2SCAN-3c/def2-mTZVPP`, TS role `Trust 0.3` / `Calc_Hess` / `Recalc_Hess 5`;
  must converge.
- §18.1 **R4 evidence boundary**: the route level is guess-generation only;
  steps 3–9 stay at the frozen `wB97X-D4/def2-TZVP`, and no criterion consumes
  route-level numbers. No equivalence claim; deviation recorded per run.
- §18.1 **R5 failure semantics**: scan/no-frame/non-converged refinement → step
  2 failed, downstream `not_evaluated` (DG-02); never a fabricated guess.
- §18.2 reference-case evidence status (convergence evidence, not criteria
  evidence; different conformer).
- §18.3 explicit unchanged list (+ why the criteria digest does not move).
- §18.4 change-control statement.

## 3. Criteria fingerprint

`criteria_definition_payload()` contains only `schema`, `version`, `regime`,
`validator_version`, `thresholds`, `downgrade_rules`, `rejection_rules`,
`criteria`, `questions`. The route is **not** part of the frozen data.

- `criteria_definition_digest` before and after: `f89f73da4178f4db35b5eb2b8f3958a4cad8bacc5c82490ccfc6777a2c3449d3` (unchanged)
- `criteria_version`: `1.0.0` (unchanged)
- `FROZEN_THRESHOLDS`, `DEGRADATION_RULES`, `REJECTION_RULES`, `CRITERIA`,
  `QUESTION_ROWS`: unmodified
- §5 / §8 / §9 / §10 / §11 / §12 of the ADR: unmodified

Therefore §16.1's “criteria/threshold/downgrade/aggregation/rejection change ⇒
new `criteria_version`” is **not** triggered. If the orchestrator decides the
route should become a normative recipe field, a new ADR revision + approval is
still required; this amendment does not self-approve that.

## 4. Contract notes

- §4 row 2 keeps `step_id=candidate_path`, `required=true`, its evidence role
  and the frozen recipe label `task_kind=xtb_path_search` (the `recipe.json`
  fixture is unchanged, digest intact). Only the **producer** changes; the
  run records `executed_as=pes_scan_frame_batch_optimize` and the route id.
- `recipe_definition_digest` remains **not-frozen** (ADR-008 §16.4); this
  amendment does not freeze it.
- No active workflow was added; catalog/registry/edit-coverage/`_SCHEDULER_MARKERS`
  are untouched (root ANTI #23/#11).

## 5. Route id

`route.diels_alder.ts_guess.pes_relaxed_scan_frame_batch_optimize_ts.v1`

Recorded in `preflight.json` (`ts_guess_route`), each step-2 document, the
chain table and the run summary/report.

## 6. Implementation findings from the real re-run

### 6.1 Bounded-recipe parameter schema cannot carry the TS spec

Step 4 as originally wired through `BoundedRecipe` failed immediately:

```
RecipeSchemaError: step.parameters.ts: unsupported parameter value type dict
```

`cccp.calculation.recipes.contracts._check_parameter_value` accepts only
scalars/lists. The nested `ts: {enabled, mode_index}` cannot travel through the
envelope, so step 4 uses the **real task core** (`cccp.calculation.run_optimize`)
— the same execution station as steps 1, 2, 6 and 9. This is an entry choice,
not a level change.

### 6.2 Frozen-level TS optimization does not converge

At `wB97X-D4/def2-TZVP` the TS optimization took 5790 s for 40 cycles and
stopped at ORCA's `MaxIter` with the explicit native error block
(*“The optimization did not converge but reached the maximum number of
optimization cycles”*). A warm restart from its final geometry
(`Trust 0.3`, 100 cycles) lost the negative eigenvalue at cycle 21 (0 imaginary
modes) and drifted to a minimum; it was stopped. The frozen-level attempt is
preserved in `frozen-level-attempt/` with `ts_optimize = failed`.

The native marker is authoritative: the acceptance now treats
`did not converge` as non-convergence even when the task payload summarises
`optimization_status = converged`.

### 6.3 R6 reference-level variant

Amendment A R6 (new) allows the whole chain to run at the reference-case level
`r2SCAN-3c/def2-mTZVPP` when the frozen-level TS optimization fails. All energy
participants (steps 1, 3, 4, 5, 7, 8, 9) use the same level; the deviation is
recorded in `preflight.executed_level` and the summary
(`deviation_from_frozen = true`, `equivalence_claimed = false`). No frozen and
reference numbers are mixed in one claim.

At the reference level the route's BatchOptimize `opt_freq` TS optimization
*is* the step-4 optimization (same method/basis, converged in 11 cycles), so the
acceptance consumes that receipt instead of re-running `OptTS` on an already
converged geometry (which makes ORCA step off the stationary point). Step 4 is
recorded as `route_batch_optimize_ts` with its native `ts_opt.out`,
`optimization.json` and converged gradient thresholds.

### 6.4 Resume correctness fixes

- `_existing_step` reuses only `status = completed` documents (a `partial`
  thermochemistry document is recomputed when its inputs change).
- A resumed `endpoint_prep` reloads the per-species child documents, so the
  barrier comparison does not see null reactant energies.
- `ts_optimize` reuse is refused when the native log says the optimization
  did not converge.
- The honest-negative degradation document is reused when `--reuse` is set
  (the scenarios are independent of the main chain).

## 7. Resource alignment (2026-10-28)

The route-a chain originally requested `nproc=4`, `maxcore=3000` and the PES
planner saw no `resources.nproc` (its SP workers defaulted to 1 core). It now
matches the validated reference jobs (`nproc=16`, `mem=32GB`):

- `resolve_software()` → `executables.orca.nproc=16`, `maxcore=2000`,
  `resources.nproc=16`, `resources.mem=32GB`;
- `_write_task_config()` writes the same values for freeze builders;
- task-core and bounded payloads carry `cores/nproc=16`, `maxcore=2000`,
  `mem=32GB`;
- PES single points split 16 cores into 4 workers × 4 cores
  (`_SP_MAX_NPROC_PER_JOB=4`), so no frame exceeds `-P 4`;
- `h4.json` records the admission limits (`max_cores=256`,
  `max_memory_mb=1048576`) and `within_admission_limits=true`;
- the generated `.inp` files were audited: relaxed scan 16, PES SP 4 (×4
  workers), Batch TS 16, frequencies 16, IRC 16, endpoint SP 16
  (`resources-audit.json`).

Wall time 4-core → 16-core: total QC 4351 s → 2271 s (1.92×), with the same
scientific verdict. See
`../m4-04-route-a-16core/resources-and-walltime.md`.
