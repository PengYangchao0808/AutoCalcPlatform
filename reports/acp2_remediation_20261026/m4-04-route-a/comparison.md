# M4-04 route re-run — comparison with the first failure

## 1. Three runs side by side

| run | chain | scenario_result | claim_status | H4 |
| --- | --- | --- | --- | --- |
| first M4-04 (`reports/acp2_remediation_20261026/m4-04/`) | step 2 `xtb_path_search` failed (`xtbpath.txt/xtbpath.xyz` not found); steps 3–8 skipped | **FAIL** | **inconclusive** | open |
| Amendment A, frozen level (`frozen-level-attempt/`) | steps 1–3 completed; step 4 TS optimization did not converge (40 cycles, then a 100-cycle warm restart drifted to a minimum); steps 5–8 skipped | **FAIL** | **inconclusive** | open |
| Amendment A, reference level (this directory) | all nine steps completed with native QC | **PASS** | **refuted** (DA-C05) | open |

`scenario_result` and `claim_status` are recorded separately in every run; the
PASS in the final run does **not** mean the scientific claim holds.

## 2. Criteria, per run

| criterion | first failure | frozen-level attempt | reference-level final |
| --- | --- | --- | --- |
| DA-C01 ts single imaginary mode | not_evaluated | inconclusive | **supported** |
| DA-C02 ts mode mapping | not_evaluated | supported | **supported** |
| DA-C03 frequency evidence | not_evaluated | inconclusive | **supported** |
| DA-C04 IRC bidirectional | not_evaluated | not_evaluated | **supported** |
| DA-C05 endpoint association | inconclusive | inconclusive | **refuted** |
| DA-C06 energy comparability | inconclusive | not_evaluated | **supported** |
| DA-C07 reactant stoichiometry | supported | inconclusive | **supported** |
| path-connectivity question | inconclusive | inconclusive | **refuted** |
| activation-energy question | inconclusive | inconclusive | **supported** (electronic) |

Final level: `r2SCAN-3c/def2-mTZVPP` (Amendment A R6 reference-level variant;
`preflight.executed_level.deviation_from_frozen = true`,
`equivalence_claimed = false`). The frozen `wB97X-D4/def2-TZVP` attempt is
preserved in `frozen-level-attempt/`; its numbers are not merged into the final
claim.

## 3. What the route fixed

1. **Step 2 now produces a real TS guess.** The PESsearch relaxed scan (21
   frames, GFN2-xTB + B97-3c) completed in 281 s and the explicit rule selected
   **frame 005** (`reaction_progress = 0.6329311284384961`) — the same frame the
   operator selected manually on the validated reference case. Frame 004
   (`0.0896`) is below the midpoint, frame 009 (`0.7205`) exists but is not
   selected. The BatchOptimize `opt_freq` TS refinement (521 s) converged in 11
   cycles and gave a first-order saddle.
2. **The candidate frequency at the working level is a real TS:** exactly one
   imaginary mode, `−457.61 cm⁻¹`, 42 real modes.
3. **All nine ADR steps completed** with native artifacts (447 files in
   `native-digests.json`), including a bidirectional IRC: forward 24 steps,
   reverse 47 steps, both converged and ORCA terminated normally.
4. **Degradation scenarios still pass** (one-sided IRC, missing frequency,
   endpoint mismatch, forged record).

## 4. Why DA-C05 is refuted — and what it does not mean

The IRC is real and bidirectional, but neither endpoint reproduces a **frozen**
endpoint within `coordinate_abs_tolerance_angstrom = 0.001`:

| direction | steps | final energy (Ha) | forming bonds (Å) | RMSD to frozen reactants | RMSD to frozen product |
| --- | --- | --- | --- | --- | --- |
| forward | 24 | −234.562005562728 | 1.547 / 1.547 | 2.7305 | **0.4518** |
| reverse | 47 | −234.490995508226 | 3.005 / 3.005 | 2.7621 | 0.5619 |

- `forward` reaches a **cyclohexene-like** geometry (product side), `reverse`
  reaches **separated fragments** (reactant side). The ORCA direction labels
  are therefore opposite to the script's step-7/8 labels; the ADR already says
  the direction label is never trusted, and DA-C05 is evaluated by identity.
- The frozen `reactants.xyz` / `product.xyz` are **constructed geometries**
  (`constructed; not an optimized or measured geometry`). A real IRC reaches a
  relaxed conformer / separated-fragment arrangement, not those exact
  coordinates. Under the frozen whole-system 0.001 Å rule, the identity check
  is refuted — even though the IRC is connected and both directions converged.
- Therefore the refutation is a **frozen-endpoint comparison consequence**, not
  an execution failure and not evidence that the IRC is disconnected. Resolving
  it requires a new `criteria_version` (for example optimized frozen endpoints,
  or per-fragment alignment for a dissociated side) — an ADR change that this
  run must not make unilaterally.

The electronic barrier is computed from `endpoint_prep` reactant energies and
the TS frequency energy at the same level:

`ΔE‡ = E(TS) − [E(butadiene) + E(ethene)] = 15.15 kcal/mol`
(TS −234.472954154901; butadiene −155.940064721503; ethene −78.557039654446).

The product-side energy is not used in that subtraction; the activation-energy
question is `supported` (electronic only; the Gibbs barrier is `not_evaluated`
because Shermo is not installed and the free-energy comparison records
`frequency_not_computed`).

## 5. H4

H4 stays **OPEN** and is not forced closed:

- `DA-ENV-LOCAL`: `scenario_result = PASS`, all nine steps completed, but the
  row is missing `walltime_process_termination` and the terminal usage receipt
  (`M1-04` capabilities still `NOT_VERIFIED`).
- `DA-ENV-LSF`: `NOT_VERIFIED` — no remote submission/termination/terminal
  receipt (M4-03 dependency).
- Budget timing basis remains `unmeasured` (ADR-008 §6).

## 6. Real wall time

| leg | seconds |
| --- | --- |
| endpoint_prep (3 optimizations, r2SCAN-3c) | 367.31 |
| candidate_path (PESsearch 281.07 + BatchOptimize TS 521.33) | 802.40 |
| candidate_frequency | 94.20 |
| ts_optimize (BatchOptimize TS receipt, R6) | 521.33 |
| ts_frequency | 94.51 |
| irc (forward + reverse) | 2454.71 |
| endpoint_reactants / endpoint_product | 8.16 / 8.17 |
| energy_thermochemistry (electronic, deterministic) | 0.0 |
| **QC total** | **~4351 s (72.5 min)** |

The frozen-level attempt additionally spent 2525.79 s on endpoint prep, 859.07 s
on the route, 383.55 s on the candidate frequency, 5790.09 s on the first
40-cycle TS optimization (observed in the step receipt before the warm restart)
and 3197.16 s on the interrupted warm restart before the R6 variant was
declared.

## 7. Open issues handed back to the orchestrator

1. **DA-C05 vs constructed frozen endpoints** — the 0.001 Å whole-system rule
   cannot be met by a real IRC endpoint. A criteria-version decision is needed;
   this run does not change it.
2. **Step 4 on an already-converged TS** — ORCA's `OptTS` steps off a converged
   stationary point; the R6 variant consumes the route's BatchOptimize TS
   optimization instead of re-optimizing. The frozen-level run shows the same
   optimizer cannot reach the tight gradient threshold in 40/100 cycles and
   drifts to 0 imaginary modes.
3. **Upstream convergence summary** — the ORCA log says “The optimization did
   not converge but reached the maximum number of optimization cycles”, while
   the task payload says `optimization_status = converged`. The acceptance now
   reads the native marker (`did not converge`) and refuses the geometry; the
   task-core parser itself remains to be fixed (outside this lane).
4. **Bounded-recipe parameter schema** — nested `ts` mappings cannot travel
   through `BoundedRecipe` (`RecipeSchemaError: step.parameters.ts: unsupported
   parameter value type dict`), so step 4 runs through the real task core.
5. **Local row receipts** — `walltime_process_termination` and the terminal
   usage receipt are still missing (M1-04/M1-03).

## 8. 16-core resource-aligned rerun

A fresh rerun with the reference-case envelope (`nproc=16`, `mem=32GB`,
`maxcore=2000`; PES single points 4 workers × 4 cores) is in
`../m4-04-route-a-16core/`. It reproduces the same verdict
(`scenario_result=PASS`, `claim_status=refuted`, H4 open) and the same
frame-005 / −457.61 cm⁻¹ / IRC endpoints, with QC total **2271 s** vs
**4351 s** for the 4-core run (1.92×; see
`../m4-04-route-a-16core/resources-and-walltime.md`). The 4-core evidence in
this directory is preserved unchanged.
