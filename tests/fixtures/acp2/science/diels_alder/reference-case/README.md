# Diels–Alder TS-guess reference case (real, same host)

Read-only copy of two **completed** jobs from the operator's run root, used as
convergence evidence for ADR-008 **Amendment A** (TS-guess acquisition route):

| leg | job | workflow | wall time |
| --- | --- | --- | --- |
| 1 | `20261014_165743_003_PESsearch` | PESsearch (ORCA relaxed scan, 21 points) | 71.46 s |
| 2 | `20261014_170039_004_BatchOptimize` | BatchOptimize `opt_freq` (TS role) | 185.14 s |

Source work dirs (never modified):

- `/home/xieningke/ACP_runs/uncategorized/3_PESsearch__02`
- `/home/xieningke/ACP_runs/uncategorized/PESsearch_TS_candidate_pes_ts_frame_005_manual_BatchOptimize`

## What is here

| path | content |
| --- | --- |
| `provenance.json` | source jobs, selected frame, per-file sha256, TS frequency facts |
| `pes/scan_config.json` | verbatim job scan request (GFN2-xTB relaxed scan, B97-3c SP, 21 points, atoms 4–5 and 1–0, 1.5181→3.5181 Å) |
| `pes/pes_profile.json` | verbatim `pes_profile_v2` (21 frames, profile sha256 `044fcbe4…`) |
| `pes/pes_review.json` | verbatim manual review: `pes_ts_frame_005`, role TS, `selection_source=manual` |
| `pes/scan_trajectory.json` | verbatim scan trajectory |
| `pes/selected_frame_005.xyz` | the frame the operator selected (sha256 `59cb1a49…`) |
| `pes/frame_009_unselected.xyz` | frame 009, kept only to document that it exists and was **not** selected |
| `batch/spec-summary.json` | profile/levels/TS role settings and derived stage timings |
| `batch/input.xyz` | the BatchOptimize input (`TAG: TS`, `candidate_id=pes_ts_frame_005`, `selection_source=manual`) |
| `batch/ts_opt.inp`, `batch/freq.inp` | verbatim ORCA inputs (`r2SCAN-3c OptTS TightOpt TightSCF`, `Trust 0.3`, `Calc_Hess`, `Recalc_Hess 5`) |
| `batch/optimization.json` | verbatim optimization receipt (converged in 8 cycles) |
| `batch/result_manifest.json`, `batch/ts_optimized.xyz` | verbatim result manifest and optimized TS geometry |
| `batch/normal_modes.json` | verbatim frequency normal modes (42 modes) |
| `batch/native-excerpts.txt` | line-numbered excerpts of `ts_opt.out` / `freq.out` (native termination, single imaginary mode at native index 6, −456.57 cm⁻¹) |

## Frame-selection rule check

Amendment A selects the first converged, constraint-satisfied frame with
`reaction_progress >= 0.5`:

- frame 4 → `0.08891975972639253` (below)
- frame 5 → `0.6346877412644145` (**selected** — the operator's manual pick)
- frame 9 → `0.7207161535591919` (exists, never selected)

The rule therefore reproduces the manual selection without reading the review
record at run time.

## Difference from the remembered frame

The operator recollection mentioned “frame 9”, but the durable evidence is
frame **005**: `pes_review.json` (revision 1), `RESULT/result_manifest.json`,
the BatchOptimize job payload/source ref (`pes_ts_frame_005`, checksum
`59cb1a49…`) and the work-dir name all bind frame 005. No record of a frame-009
selection exists in the run root or in `acp_jobs.db`. Files win; frame 009 is
kept here as an explicitly unselected artifact.

## Scope

These files are **not** ADR-008 criterion evidence. The reference run used
`r2SCAN-3c/def2-mTZVPP` (TS refinement) and `GFN2-xTB`/`B97-3c` (scan), while
the frozen evidence layer remains `wB97X-D4/def2-TZVP`. The reference case
proves the *route converges*; it does not move a threshold, a criterion or a
claim.
