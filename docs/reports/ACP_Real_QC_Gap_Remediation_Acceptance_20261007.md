# ACP Real-QC Gap Remediation — Acceptance Report (2026-10-07)

**Plan:** `.omo/plans/real-qc-gap-remediation.md` (todo 14 / T13) · **Source:** `docs/ACP_Real_QC_Gap_Remediation_Plan.md`
**Baseline audit:** `docs/reports/ACP_Real_QC_Gap_Audit_20261006.json` (HEAD `32d4748`, reported baseline `a90d00b`)
**Accepted tree:** `f611c42` + two uncommitted T13 files (see §6) · **Date:** 2026-10-07
**Evidence:** `.omo/evidence/real-qc-gap-remediation/task-14-acceptance/` (matrix.json, full-suite*.log, service-restart.log, per-case receipts)

> **CLOSURE SEMANTICS.** All ten required cases were actually run on this host's production binaries and **PASS**. One discovered defect (NMR default ORCA functional) is reported in §5 and **requires a follow-up**; it does not invalidate the ten cases (see the NMR deviation note). Nothing here is a nominal/simulated pass.

---

## 1. Remediation summary (commits `5b7e932..f611c42`)

| Commit | Gap | Change |
|---|---|---|
| `5b7e932` | D1 | Parse real ORCA `.hess` block format, `$atoms` geometry, indexed frequencies |
| `c7d67e0` | D2a | `acp run scan` materializes SMILES → traceable XYZ with provenance |
| `b7db31b` | D3 | NMR stage-resolved solvent models (sampling / DFT / GIAO) |
| `97e9432` | D1 | tsmode raw-byte InHess handoff + SVD-rank external subspace (linear 3N−5) |
| `f3bf650` | D5 | ORCA 6.1.1 CASSCF convergence/occupations/roots parsing |
| `6a8e1b7` | D3 | Confsearch staged solvent defaults + model-aware protocol identity |
| `b1fa9f2` | D5 | Task-level CAS convergence gating + conservative recovery re-judgment |
| `734222a` | D2b | `ScanOptions.use_scants` default-off with full projection surface |
| `26e280c` | D6 | Publish real `RESULT/energy/<step>.json` for energy products |
| `f611c42` | D7 | Manual/README/CLI-help alignment with fixed scan/tsmode/IRC/solvent semantics |
| (T13, uncommitted) | — | NMR CLI `None`-DP5 summary robustness fix (§6) |

`acp.calculations.primitives` / `acp.backends` remain pure compat shims; `cccp` imports no `acp` (grep gates green).

## 2. Ten-case real matrix (all PASS)

| # | Case | Result | Key observable |
|---|---|---|---|
| 1 | Plain scan, water O–H 1.05→1.25 Å, 3 pts, **no ScanTS** | **PASS** | 3/3 frames; energies `−76.41475646/−76.39847304/−76.3775457` Eh (exact audit `scan_control`); distances 1.05/1.15/1.25 Å; no ScanTS token |
| 2 | SMILES scan `CCO`, 3 pts (`--scan-points 3`) | **PASS** | `input_source.json` provenance (CCO, ETKDGv3 seed 42, charge 0/mult 1); 3/3 frames readable |
| 3 | Explicit ScanTS — negative + positive | **PASS** | NEG: exit 1, ORCA `ScanTSRun: … highest energy is the first or last point … no scanTS run possible`. POS: dihedral ethane 60→180 (interior max 120°), exit 0, real `TS OPTIMIZATION` + `SECOND TS OPTIMIZATION WITH HYBRID HESSIAN` + converged |
| 4 | tsmode (frozen bundle, native imaginary index 6) | **PASS** | staged InHess sha256 `7dd26657…`; OptTS converged; `mode_correspondence=consistent` overlap `0.999999768596934`; single imaginary `−1123.21 cm⁻¹` |
| 5 | NMR ethanol/chloroform — censo-zero then censo-light | **PASS\*** | real CREST+CENSO+ORCA GIAO; sampling `alpb` defaulted (xTB `--alpb chcl3`), CENSO `sm=smd`, GIAO `CPCM(chloroform)`; 4 matched observations; reports written; both exit 0 |
| 6 | Confsearch solvent regression (real CREST) | **PASS** | CREST argv `… -gfn 2 --chrg 0 -uhf 0 -ewin 6.0 --alpb chcl3` (**no smd**); CENSO DFT model preserved (`sm=smd`) |
| 7 | OrcaGradient (timeout 600 + config `timeout:null`; null payload) | **PASS** | both exit 0; `−76.418906924267` Eh; `grad.engrad` 3N=9, hartree/bohr; timeout propagation + caller-config-unchanged (T03 tests, 4 passed) |
| 8 | CASSCF real + non-convergence + recovery refusal | **PASS** | CAS(2e,2o) converged=true, occupations `[1.99733,0.00267]`, `−75.976220169701` Eh; probe: not-converged → failed / `NOT_CONVERGED`; recovery refuses false/SCF-only/truncated logs, accepts converged |
| 9 | Energy publication — five step kinds + interrupted resume | **PASS** | real `RESULT/energy/*.json` for singlepoint/optimize/frequency/xtb_optimize/casscf, value==scientific energy, unit hartree; T12 tests 31 passed (incl. publish-interrupted zero QC recompute) |
| 10 | API (fixed-code isolated server) | **PASS** | scan/CAS/NMR submitted (201 queued) → all completed; detail/files/structure-viewer all HTTP 200; server shut down, port free, no leftover process |

**\* NMR deviation (transparent):** ORCA 6.1.1 rejects the workflow's default GIAO level `mPW1PW91` (see §5). The case was run with the NMR-allowed ORCA-valid level **B3LYP/def2-TZVPP** + `--error-model placeholder-student-t`, so DP4/DP5 are placeholder-relative (not publication values). All workflow stages and required observables were exercised on real software.

## 3. Gates

| Gate | Result |
|---|---|
| `pytest -m "not slow"` | **7633 passed, 13 skipped, 0 failed** (945.73 s) on `f611c42`; final tree (with cli.py fix) **7634 passed, 13 skipped, 0 failed** (967.66 s) → `full-suite-postfix.log`; **independently re-run on the same final tree** in T13 resumed verification → **7634 passed, 13 skipped, 0 failed** (964.52 s) → `full-suite-reverify.log` |
| Targeted §6 defect suites (D1–D8) | **851 passed, 0 failed** |
| Architecture triple (`test_architecture_invariants`, `test_f4_scope_audit`, `test_grep_gates_script`) | **204 passed** |
| `scripts/check_grep_gates.py --suite architecture-remediation` | **exit 0** (0 blocking, 0 stale) |
| compileall | 3.11.13 OK; 3.13.9 OK; **3.9.25 unsupported** (<3.10 `match` — project requires ≥3.10) |
| ruff delta vs baseline `32d4748` | **DELTA = 0** — v0.4.0 (pinned): 1580 errors / 195 format files both sides; venv v0.16.6: 1040 / 163 both sides |

## 4. Service

- `sudo systemctl restart acp` **could not be performed** — this host has no passwordless sudo (`sudo -n true` → password prompt). Recorded verbatim in `service-restart.log`.
- Service still loaded from **2026-10-06 16:40:16** (`ActiveEnterTimestamp`); repo HEAD `f611c42` committed **2026-10-07 05:05**. **Operator must restart** to load the fixed code. The pre-existing service (PID 2938402, `:8765`) was left untouched; API acceptance used an isolated user-level server on `:8799`.

## 5. Discovered defects (follow-up required)

1. **`D-NMR-ORCA61-FUNCTIONAL` (blocking for NMR defaults).** ORCA 6.1.1 rejects `mPW1PW91` (`UNRECOGNIZED OR DUPLICATED KEYWORD(S): MPW1PW91`); the binary exposes the functional as **`mPW1PW`** (`strings orca611/orca` → `MPW1PW`/`MPWPW`). Direct ORCA test: `mPW1PW91`/`MPW1PW91`/`PW1PW91` REJECTED; `mPW1PW`/`B3LYP`/`PBE0`/`wB97X-D4` OK. `mPW1PW` is not declared in `METHOD_META`, so `resolve_nmr_method` refuses it. Consequence: `acp run nmr` with defaults cannot run on this production ORCA. **Recommended follow-up:** add a version-aware ORCA functional alias (`mPW1PW91` ↔ `mPW1PW`) or declare `mPW1PW` in `METHOD_META` + keyword-registry policy — **without** silently changing the Goodman error-model level. Larger scope than T13; **not fixed here**.
2. **`D-NMR-CLI-NONE-DP5` (low; fixed in T13).** `_handle_nmr` raised `TypeError: float() argument must be … not 'NoneType'` when the winner `dp5` is `None` (placeholder error model): the workflow completed and wrote its report, but the CLI exited 1. Fixed minimally in `src/acp/cli.py` (render DP4/DP5 as `N/A` when `None`) with regression test `tests/test_acp_cli.py::test_handle_nmr_tolerates_none_dp5_in_summary`.

## 6. Changed files (T13)

| File | Change | Test evidence |
|---|---|---|
| `src/acp/cli.py` | `_handle_nmr` winner-summary tolerates `None` DP4/DP5 | `tests/test_acp_cli.py -k nmr` → 6 passed; ruff clean |
| `tests/test_acp_cli.py` | new `test_handle_nmr_tolerates_none_dp5_in_summary` | included above |

No other source files were modified. The two untracked docs (`ACP_Real_QC_Gap_Remediation_Plan.md`, `ACP_Real_QC_Gap_Audit_20261006.json`) are plan inputs, intentionally left untracked. **Not committed** (per brief).

## 7. 14-workflow acceptance matrix

| # | Workflow | Level at this HEAD | Notes |
|---|---|---|---|
| 1 | Confsearch | **scientific (real re-run)** | `xtb-crest` + solvent (case 6); CENSO DFT path via NMR censo-light |
| 2 | PESsearch | **artifact / unchanged** | not re-run; unit suites green; original report stands |
| 3 | BatchOptimize | **artifact / unchanged** | not re-run; unit suites green |
| 4 | XtbPathSearch | **artifact / unchanged** | not re-run; unit suites green |
| 5 | OrcaGradient | **scientific (real re-run)** | case 7 |
| 6 | irc | **artifact / unchanged** | not re-run; unit suites green |
| 7 | scan | **scientific (real re-run)** | cases 1–3 |
| 8 | tsmode | **scientific (real re-run)** | case 4 |
| 9 | casscf | **scientific (real re-run)** | case 8 |
| 10 | nmr | **scientific (real re-run, deviated level)** | case 5; see §5.1 |
| 11 | singlepoint | **scientific (real re-run)** | case 9 |
| 12 | optimize | **scientific (real re-run)** | case 9 |
| 13 | frequency | **scientific (real re-run)** | case 9 |
| 14 | xtb_optimize | **scientific (real re-run)** | case 9 |

Unchanged workflows (PESsearch/BatchOptimize/XtbPathSearch/irc) were **not** re-run on real binaries this wave; their coverage rests on the full unit suite (7633 passed) and the original audit. No claim of current-HEAD real execution is made for them.

## 8. Exact versions

| Component | Version | Path |
|---|---|---|
| Test Python | **3.11.13** | `/opt/acp/venv/bin/python` |
| Python present | 3.11.13 (`/usr/bin/python3.11`), 3.13.9 (`/home/<user>/anaconda3/bin/python`), 3.9.25 (`/usr/bin/python3.9`, **unsupported <3.10**) | — |
| ORCA | **6.1.1** | `/home/<user>/orca611/orca` |
| CREST | **3.0.2** | `/home/<user>/crest/crest` |
| xTB | **6.7.1** | `/home/<user>/xtb-dist/bin/xtb` |
| CENSO | **3.0.8** | `/home/<user>/anaconda3/bin/censo` |
| ISOSTAT | molclus 1.14 | `/home/<user>/molclus_1.14_Linux/isostat` |
| Shermo | 2.6.1 | `/home/<user>/Shermo_2.6.1/Shermo` |
| ruff (venv / pinned) | 0.16.6 / 0.4.0 | `/opt/acp/venv/bin/ruff` |

**Environment difference vs original report:** the original report was written against the remote node Python **3.13.9**; this acceptance used the local test interpreter **3.11.13**. Local PATH resolves CREST/xTB (the original report's CREST/xTB skip no longer reproduces). No claim is made that the original skip state is reproduced.

## 9. Unverified / blocked items

- **Service restart** — blocked by missing passwordless sudo; operator action required (§4).
- **NMR default GIAO level** — blocked by `D-NMR-ORCA61-FUNCTIONAL`; case run with a valid level instead.
- **PESsearch / BatchOptimize / XtbPathSearch / irc real runs** — not re-run (unchanged paths; §7).

## 10. Cleanup receipt

- Isolated API server (`:8799`) shut down; port free; only the pre-existing system service (`:8765`) remains. No leftover ORCA/CREST/xTB/python servers.
- Isolated run root `/tmp/acp_gap_accept` (native xfs) used throughout; per-case receipts copied into `.omo/evidence/real-qc-gap-remediation/task-14-acceptance/`.
- Temporary baseline ruff worktree `/tmp/acp_baseline_ruff` removed.
- Repo worktree: only `src/acp/cli.py` + `tests/test_acp_cli.py` modified; test byproducts (`e2e-task-root.txt`, `grad.engrad`) reverted/removed.
- Historical jobs/data (e.g. `20261005_213230_002_Confsearch`) untouched. Not committed.
