# ORCA 6.1.1 real-format fixtures (T02)

Byte-identical freezes of real ORCA 6.1.1 artifacts captured on **2026-10-06** from `/tmp`
(volatile). Copied with `read_bytes`/`write_bytes` — no newline/encoding rewrites. Source and
destination sha256 were verified equal for every file at freeze time.

Expected values used by the regression tests come from the independent decode in
`docs/reports/ACP_Real_QC_Gap_Audit_20261006.json` (sections `D1`, `D5`,
`D5_scf_false_positive`) — never from the parsers under test.

Only `.hess` / `.out` / `.xyz` / `.json` / `.md` files are frozen; wavefunction binaries
(`.gbw`, `.densities`, `property.txt`, …) are intentionally excluded.

## Files

| File | Source path (2026-10-06) | Method / system | Geometry | ORCA | sha256 |
|---|---|---|---|---|---|
| `water_freq.hess` | `/tmp/acp_mt/freq/water/WORK/04_FREQ/freq.hess` | r2SCAN-3c Freq (Hessian in atomic units, frequencies in cm⁻¹) | H₂O, charge 0, mult 1; input XYZ (Å): O 0.000000 0.000000 0.117300; H 0.000000 0.757200 −0.469200; H 0.000000 −0.757200 −0.469200. `$atoms` stores the mass-centered geometry in **Bohr**: O (0, −0, 0.124028972808); H (0, 1.430900628605, −0.984295404737); H (0, −1.430900628605, −0.984295404737) | 6.1.1 (verified from sibling `freq.out` `Program Version` banner) | `3d4459d8c69abe661fb05f816d05a58a67b0f2f53e6a357ac05d8de44875c42b` |
| `ts_freq.hess` | `/tmp/acp_mt/tsmode_src/freq.hess` | r2SCAN-3c Freq (real TS: source mode 0 = −464.7199 cm⁻¹) | HCN, charge 0, mult 1; input XYZ (Å): C 0.000000 0.000000 0.000000; N 1.200000 0.000000 0.000000; H 0.600000 1.039230 0.000000. `$atoms` (Bohr, mass-centered): C (−1.217574895108, −0.073246909305, 0); N (1.050096465597, −0.073246909305, 0); H (−0.083739214756, 1.890613180850, 0) | 6.1.1 (sibling `freq.out`) | `351947b14fdb75739565e40e9df04635f4483dffdab6393d41e1bf2889e259a0` |
| `casscf_water.out` | `/tmp/acp_mt/casscf/water/WORK/08_CASSCF/casscf.out` | `! ma-def2-SVP def2/J CASSCF TightSCF`; `%casscf nel 2 norb 2 mult 1 nroots 1` (ROOT 0 CAS energy = −75.976220169701 Eₕ) | H₂O, charge 0, mult 1: O 0.000000 0.000000 0.117300; H 0.000000 0.757200 −0.469200; H 0.000000 −0.757200 −0.469200 | 6.1.1 (own `Program Version` banner) | `d1a07e81ccf578853bfea5ccb46ec88fe300f9dcad6a1a3f0c1055907aa21d95` |
| `scf_without_casscf.out` | `/tmp/acp_gap_review/scf_without_casscf.out` | Minimal SCF-only excerpt (2 lines: `THE SCF HAS CONVERGED` + `FINAL SINGLE POINT ENERGY -75.0`) — audit probe input for the D5 SCF false-positive; contains **no** CASSCF section | n/a (no geometry) | n/a (hand-trimmed excerpt; audit `D5_scf_false_positive`) | `5642a5d20bf7108c9fb50ac2cd8f039bf5fd554414fcb900a3ae96170f7d6031` |
| `ts_opt.out` | `/tmp/acp_mt/ts_opt_m0/ts_opt.out` | `! r2SCAN-3c OptTS NumFreq`; `%geom InHess Read / InHessName "freq.hess" / TS_Mode {M 0}` | HCN, charge 0, mult 1; input XYZ (Å): C 0.000000 0.000000 0.000000; N 1.200000 0.000000 0.000000; H 0.600000 1.039230 0.000000 | 6.1.1 (own banner) | `95d4392ac33e4c7ee436b0e8e7ca8b28a44009968763957ee69aa2a2f8a28775` |
| `ts_opt.hess` | `/tmp/acp_mt/ts_opt_m0/ts_opt.hess` | Hessian written by the OptTS+NumFreq run above (**independent file — not** the input `freq.hess`) | Final OptTS geometry in mass-centered Bohr (see `ts_opt.xyz` for the Å rendering) | 6.1.1 | `7dd266577567c38bb2419aa5293a10cfe0e77d99ab8acdf4eaf9510874501334` |
| `ts_opt.xyz` | `/tmp/acp_mt/ts_opt_m0/ts_opt.xyz` | Final OptTS geometry (header: `Coordinates from ORCA-job ts_opt E -93.324592770469`) | HCN (Å): C 0.04771464009367 0.02337916244003 0.00000000000000; N 1.22629221831717 −0.09144669525812 0.00000000000000; H 0.52599314158916 1.10729753281809 0.00000000000000 | 6.1.1 | `5319d4ce17c6013afd1902cc9f9ea5e8b1c543a6a4ebd53e17ae973aab4839ba` |

## `ts_opt_bundle.json`

CLI-consumable `tsmode_bundle_v1` description with file-relative refs
(`files.output` / `files.hessian` / `files.geometry` → the three `ts_opt.*` files in this
directory), minimal `source` metadata, charge/multiplicity 0/1, and `level`
(method `r2SCAN-3c`, ORCA 6.1.1). Enables

```bash
acp run tsmode --source-bundle tests/fixtures/qc/orca61/ts_opt_bundle.json \
  --source-mode-index 0 --output <task_dir>
```

without any `/tmp` dependency (relative refs resolve against the bundle's directory).

## Dedup note

`/tmp/acp_mt/ts_opt_m0/freq.hess` is the **same byte file** as `ts_freq.hess`
(sha256 `351947b14fdb75739565e40e9df04635f4483dffdab6393d41e1bf2889e259a0` — it is the
input Hessian staged for the OptTS run). It is **not** stored a second time here
(`freq.hess` intentionally absent; guarded by
`tests/test_hess_file_real_fixtures.py::test_deduped_freq_hess_not_duplicated`).
`ts_opt.hess` (sha256 `7dd26657…`) is a different, independent file and is frozen separately.
