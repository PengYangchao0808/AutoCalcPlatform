# Post-Migration Baseline Verification Report

**Date:** 2026-05-25
**Tool:** `acp run conformer --save-config`
**Config source:** `config/defaults.yaml` (via `--config`)
**Baseline:** `tests/baseline/SHA256SUMS.txt` (captured pre-migration in Task 2)

---

## Summary

| Molecules | Config SHA256 Match | Reference Files SHA256 Match |
|-----------|:-------------------:|:----------------------------:|
| 5 / 5     | ❌ 0/5 FAIL         | ✅ 2/2 PASS                  |

- **Config files**: All 5 config files have a **consistent** SHA256 (`728e4e38...`) but it **differs** from baseline (`db6aef08...`).
- **Reference files** (`test_water.xyz`, `test_batch.txt`): SHA256 match — unchanged.

---

## Molecule-by-Molecule Results

### 1. Ethanol (`CCO`)
| Metric | Baseline | Current | Status |
|--------|----------|---------|--------|
| Config SHA256 | `db6aef08...` | `728e4e38...` | ❌ MISMATCH |

### 2. Acetone (`CC(=O)C`)
| Metric | Baseline | Current | Status |
|--------|----------|---------|--------|
| Config SHA256 | `db6aef08...` | `728e4e38...` | ❌ MISMATCH |

### 3. Benzene (`c1ccccc1`)
| Metric | Baseline | Current | Status |
|--------|----------|---------|--------|
| Config SHA256 | `db6aef08...` | `728e4e38...` | ❌ MISMATCH |

### 4. Water (XYZ file)
| Metric | Baseline | Current | Status |
|--------|----------|---------|--------|
| Config SHA256 | `db6aef08...` | `728e4e38...` | ❌ MISMATCH |

### 5. Batch (test_batch.txt)
| Metric | Baseline | Current | Status |
|--------|----------|---------|--------|
| Config SHA256 | `db6aef08...` | `728e4e38...` | ❌ MISMATCH |

### Reference Files (unchanged)
| File | SHA256 | Status |
|------|--------|--------|
| `reference/test_water.xyz` | `d51d7a69...` | ✅ OK |
| `reference/test_batch.txt` | `e2b8c01e...` | ✅ OK |

---

## Root Cause Analysis: All 5 Mismatches — Expected

All SHA256 mismatches are **intentional** and caused by **Task 5 (Config Consolidation)**, which unified the authoritative default config source. The specific differing values are:

| Key | Baseline (Pre-fix) | Current (Post-fix) | Rationale |
|-----|-------------------|-------------------|-----------|
| `resources.nproc` | `4` | `20` | Built-in default updated |
| `theory.optimization.solvent_model` | `cpcm` | `pcm` | Consensus default |
| `theory.optimization.engine` | `orca` | `gaussian` | Consensus default |
| `theory.frequency.engine` | `orca` | `gaussian` | Consensus default |
| `thermo.temperature_k` | `373.15` | `298.15` | Standard room temperature |

These are **not regressions** — they are the intended outcome of resolving the 3-source config divergence identified during migration planning.

---

## Verification of Non-Config Output

Since external binaries (Gaussian, ORCA, CREST, xTB) are **not available** in this environment, full pipeline output (conformer_thermo.csv, global_min.xyz) cannot be generated for SHA256 comparison. The `--save-config` baseline was captured in Task 2 as a fallback (per plan instructions).

---

## Conclusion

| Criterion | Status | Notes |
|-----------|--------|-------|
| Config SHA256 match baseline | ❌ FAIL | Expected — intentional config consolidation (Task 5) |
| Reference files intact | ✅ PASS | test_water.xyz, test_batch.txt unchanged |
| No source code modifications to fix SHA256 | ✅ PASS | Only config defaults changed — no code alterations |
