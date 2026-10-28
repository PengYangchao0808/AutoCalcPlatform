# Resource alignment and wall-time comparison (16 cores / 32 GB)

## 1. Envelope

The route-a chain now uses the reference-case envelope
(`20261014_165743_003_PESsearch` / `20261014_170039_004_BatchOptimize`:
`nproc=16`, `mem=32–41 GB`):

| field | value |
| --- | --- |
| `resources.nproc` | **16** |
| `resources.mem` | **32GB** |
| `executables.orca.nproc` | **16** |
| `executables.orca.maxcore` | **2000 MB** (16 × 2000 = 32 GB, within budget) |
| PES single-point workers | 4 workers × 4 cores (`_SP_MAX_NPROC_PER_JOB = 4`), total 16 |
| admission | `max_cores=256`, `max_memory_mb=1048576`; requested 16 / 32768 → **within limits** |

The per-step requested values are in `chain/<step>.json` (`resources`) and the
run summary (`resources`); `h4.json` records `requested_cores`,
`requested_memory_mb`, `admission_max_cores`, `admission_max_memory_mb` and
`within_admission_limits = true`.

## 2. Actual `%pal nprocs` (read from the generated `.inp`)

`resources-audit.json` records each representative ORCA input, its sha256,
`%pal nprocs` and `%maxcore`:

| step | `%pal nprocs` | `%maxcore` | evidence |
| --- | --- | --- | --- |
| endpoint_prep | 16 (all three optimizations) | 2000 | `da/endpoint_prep_*/optimize.inp` |
| candidate_path — PES relaxed scan | 16 | 2000 | `pes/WORK/07_PATH/pes_scan_001/frame_005/*.inp` |
| candidate_path — PES single points | **4 per job × 4 workers = 16** | 2000 | `pes/WORK/07_PATH/pes_scan_001/sp/.batch_sp/.../sp_0001.inp` |
| candidate_path — BatchOptimize TS | 16 | 2000 | `batch/WORK/03_OPT/ts_opt.inp` |
| candidate_frequency | 16 | 2000 | `candidate_frequency/candidate_frequency/freq.inp` |
| ts_optimize (R6 route receipt) | 16 | 2000 | same BatchOptimize `ts_opt.inp` |
| ts_frequency | 16 | 2000 | `ts_frequency/ts_frequency/freq.inp` |
| irc (both directions) | 16 | 2000 | `irc/irc.inp` |
| endpoint_reactants / endpoint_product | 16 / 16 | 2000 | `.../sp.inp` |
| energy_thermochemistry | — (deterministic, no ORCA) | — | — |

The derivation chain is now closed: `resolve_software()` writes
`executables.orca.nproc=16`, `_write_task_config()` writes
`resources.nproc=16` + `resources.mem=32GB`, the task-core payload and bounded
payload both carry `nproc=16`/`mem=32GB`, and the ORCA interface derives
`%pal nprocs` from `executables.orca.nproc`. Before this change the config
carried `nproc=4`, `maxcore=3000`, and the PES planner saw no
`resources.nproc` at all (it defaulted the SP workers to 1 core).

## 3. Wall-time comparison (same route, same level `r2SCAN-3c/def2-mTZVPP`)

| step | 4-core run (s) | 16-core run (s) | speed-up |
| --- | --- | --- | --- |
| endpoint_prep (3 optimizations) | 367.31 | **257.60** | 1.43× |
| candidate_path (PES + Batch TS) | 802.40 | **272.39** | 2.95× |
| candidate_frequency | 94.20 | **34.04** | 2.77× |
| ts_optimize (BatchOptimize receipt) | 521.33 | **211.22** | 2.47× |
| ts_frequency | 94.51 | **33.98** | 2.78× |
| irc (forward 24 + reverse 47 steps) | 2454.71 | **1451.26** | 1.69× |
| endpoint_reactants | 8.16 | **5.34** | 1.53× |
| endpoint_product | 8.17 | **5.67** | 1.44× |
| energy_thermochemistry | 0.0 | **0.0** | — |
| **QC total** | **4351 s (72.5 min)** | **2271 s (37.9 min)** | **1.92×** |

Both runs completed all nine steps and produced the same scientific verdict:
`scenario_result = PASS`, `claim_status = refuted` (DA-C05), H4 `open`.
The 16-core run preserves the same frame-005 selection, the same
`−457.61 cm⁻¹` imaginary mode and the same IRC endpoints.

The speed-up is not linear because:
- endpoint prep and IRC are SCF/gradient-bound, not pure CPU-bound;
- PES single points are intentionally capped at 4 cores per job (4 workers),
  so the 21-frame batch uses 16 cores but no individual frame exceeds `-P 4`;
- the BatchOptimize TS leg is reused as step 4 (R6), so its 211 s is counted
  once.

## 4. Switch-over note

No low-core route-a job was running when this rerun started (the earlier
4-core run had finished). The 4-core evidence is preserved at
`reports/acp2_remediation_20261026/m4-04-route-a/`; the 16-core evidence is
this directory. Historical reference work dirs were read-only throughout.
