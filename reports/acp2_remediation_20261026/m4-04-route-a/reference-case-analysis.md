# M4-04 re-run — reference-case analysis (ADR-008 Amendment A)

- Reference fixture: `tests/fixtures/acp2/science/diels_alder/reference-case/`
- Source jobs (read-only): `20261014_165743_003_PESsearch`, `20261014_170039_004_BatchOptimize`
- Source work dirs:
  - `/home/xieningke/ACP_runs/uncategorized/3_PESsearch__02`
  - `/home/xieningke/ACP_runs/uncategorized/PESsearch_TS_candidate_pes_ts_frame_005_manual_BatchOptimize`
- Both jobs `completed`; every copied file digest is recorded in `provenance.json`
  and re-verified by `tests/test_acp2_m404_da.py`.

## 1. What the reference case actually is

A 16-atom C6H10 Diels–Alder system. PESsearch runs an ORCA **relaxed scan**
that opens the two ring bonds of a cyclohexene-like input (the reverse of the
DA bond formation) and evaluates each frame with a B97-3c single point. The
operator then selected one frame manually in the PES review surface, and
BatchOptimize refined that frame as a **TS** (`opt_freq`).

| parameter | reference value |
| --- | --- |
| job | `20261014_165743_003_PESsearch` (`3_PESsearch__02`) |
| workflow | PESsearch (`bond_length_scan`) |
| scan driver | ORCA `relaxed_scan`, `reuse_previous_geometry=true`, `full_scan=true`, `use_scants=false`, `max_iterations=250`, retry 2 |
| scan optimiser | `GFN2-xTB`, `max_iterations=250`, convergence `normal` |
| scan single point | `B97-3c`, `Tight` SCF |
| coordinates | two distances: atoms `[4,5]` 1.5181→3.5181 Å and `[1,0]` 1.4893→3.4893 Å, **21 points**, Δ = 2.0 Å |
| selection mode | functional `double_bond_scan`, atom_indices `[4,5,1,0]` |
| frames | 21, all `optimization_converged=true`, `constraints_satisfied=true`, max residual 8.6e-7 Å |
| wall time | **71.46 s** (relaxed scan 21.0 s + 21 single points 37.7 s, 16 cores) |
| review | `pes_ts_frame_005`, frame_index **5**, `role=TS`, `selection_source=manual`, profile sha256 `044fcbe4…` |
| BatchOptimize | `20261014_170039_004_BatchOptimize`, profile `opt_freq`, TAG `TS` |
| TS level | `r2SCAN-3c`/`def2-mTZVPP`; `TightOpt`, `TightSCF`, `Trust 0.3`, `Calc_Hess`, `Recalc_Hess 5`, `MaxIter 200` |
| TS optimisation | **converged in 8 cycles**, final E = `-234.472973137812` hartree |
| TS frequency | 42 modes, **one imaginary mode** at native index 6: **−456.57 cm⁻¹**, `ts_validation=passed` |
| wall time | **185.14 s** (optimise 122.2 s + frequency 38.0 s, 16 cores) |

## 2. Frame 005 vs frame 009 (recorded difference)

The recollection was "frame 9"; the durable evidence is frame **005**:

| evidence | binds |
| --- | --- |
| `pes_review.json` (revision 1, confirmed 2026-10-14T16:59:45+08:00) | `pes_ts_frame_005`, frame_index 5, manual |
| `RESULT/result_manifest.json` | product `pes_candidate_pes_ts_frame_005`, frame_index 5 |
| BatchOptimize `spec.input.source_ref` | `job_20261014_165743_003_PESsearch:RESULT/structures/pes_ts_frame_005.xyz`, checksum `59cb1a49…` |
| BatchOptimize work-dir name | `…_pes_ts_frame_005_manual_BatchOptimize` |
| `acp_jobs.db` | no `frame_009` string in any job/spec/result/decision payload |

Frame 009 **exists** in the 21-frame profile (`reaction_progress=0.7207161535591919`)
but was never selected. It is kept in the fixture as `pes/frame_009_unselected.xyz`
and explicitly marked unselected. Files win over recollection; no evidence was
rewritten.

## 3. Why this validates the Amendment A rule

The Amendment A rule — *first converged, constraint-satisfied frame with
`reaction_progress >= 0.5`* — reproduces the operator's manual choice on the
reference profile:

| frame | reaction_progress | eligible | selected |
| --- | --- | --- | --- |
| 4 | 0.08891975972639253 | yes | no |
| **5** | **0.6346877412644145** | yes | **yes** |
| 9 | 0.7207161535591919 | yes | no |

The rule is deterministic, auditable from the persisted profile, and has no
fallback pick: if no frame crosses the midpoint the route fails (`not_evaluated`
downstream, DG-02) instead of inventing a candidate.

## 4. What the reference case proves — and what it does not

Proves:

- the route (relaxed scan → explicit frame → BatchOptimize TS) converged on a
  real, same-host 16-atom C6H10 DA system, with one imaginary mode and a passed
  TS validation;
- the frame rule reproduces a real manual selection without reading the review
  record at run time;
- the two legs are fast enough to be re-run inside an acceptance slice
  (71 s + 185 s at 16 cores on the reference host).

Does **not** prove:

- that the frozen fixture geometry converges through the same route — the
  reference scan starts from a different, operator-supplied ring geometry;
- any ADR-008 criterion. The reference levels (`GFN2-xTB`, `B97-3c`,
  `r2SCAN-3c/def2-mTZVPP`) are **guess-generation levels only**. The frozen
  evidence layer remains `wB97X-D4/def2-TZVP` (ADR-008 §3). No equivalence
  claim is made and no threshold is touched.

## 5. Route-a reproduction on the frozen DA system

The same route was executed on the frozen `reactants/product` fixture (scan
start = frozen cyclohexene re-indexed into reactant atom order; the two formed
bonds are `(0,11)` and `(3,10)` in that order, start distances 1.4945 Å and
1.4781 Å, both scanned +2.0 Å over 21 points):

| quantity | reference case | route-a (frozen DA system) |
| --- | --- | --- |
| frame count | 21 | 21 |
| frame 4 `reaction_progress` | 0.08891975972639253 | 0.0896 |
| frame 5 `reaction_progress` | 0.6346877412644145 | **0.6329311284384961** |
| frame 9 `reaction_progress` | 0.7207161535591919 | 0.7205 |
| selected frame | 5 (manual) | **5 (rule)** |
| scan wall time (4 cores here) | 71 s at 16 cores | 281 s |
| BatchOptimize TS cycles | 8 | 11 |
| BatchOptimize wall time | 185 s at 16 cores | 521 s |
| TS imaginary mode | −456.57 cm⁻¹ | **−457.61 cm⁻¹** |
| TS validation | passed | passed |

The two profiles have the same shape (barrier top around frame 7–8, ring-opening
discontinuity between frames 4 and 5), and the explicit rule selects the same
frame index the operator chose manually. The reference case is therefore
reproduced on the frozen system, with the expected slower wall time from 4 vs
16 cores.
