# ACP → CCCP Workflow Call Matrix (A3/A4)

**Generated:** 2026-10-05 (plan todo 36)
**Updated:** 2026-10-06 (plan todo 33 — nmr row / chain brought to the final
task-core, checkpoint and solvent evidence)
**Scope:** the 14 active `acp run <workflow>` workflows and every legal
protocol / profile / branch, mapped to the **unique execution core**
`cccp.calculation` task entries.

This document is the human-readable companion to
`tests/test_acp_workflow_task_matrix.py`, which guards the same facts with
AST + runtime assertions.  The invariant it encodes (root `AGENTS.md`
ANTI #17):

> The ACP layer orchestrates, plans, adapts payloads and publishes products.
> **Every QC execution lands in exactly one `cccp.calculation` task core.**
> There is no second implementation of optimize / single-point / frequency /
> thermochemistry anywhere under `src/acp/`.

The `fake` demo workflow is intentionally excluded (no QC execution).

## Legend

- **ACP entry** — the public workflow entry that `acp run` / scheduler calls.
- **Branches** — every legal routing branch that must reach the same core
  (protocols, refinement policies, profiles, direction/strategy variants).
- **Task core (unique)** — the `cccp.calculation` function(s) that perform
  the QC work.  `cccp.calculation.batch.run_batch` is the shared concurrent
  batch executor; per-item work still calls the same task cores.

## Master matrix

| # | Workflow | ACP entry | Branches (all legal) | Unique task core(s) |
|---|----------|-----------|----------------------|---------------------|
| 1 | `Confsearch` | `acp.confsearch.engine.ConfsearchEngine` | protocol ∈ {`xtb-crest`, `xtb-md`, `censo-crest`, `xtbmd-censo`}; profile ∈ {`light`, `default`, `high`}; refinement_policy ∈ {`screen`, `rank1`, `cumulative-99`, `all`} | `run_conformer_search`, `run_censo_refine`, `run_md_sampling`, `run_clustering`, `run_optimize`, `run_frequency`, `run_singlepoint`, `run_thermochemistry` |
| 2 | `PESsearch` | `acp.workflows.pes_search.run_pes_search` / `run_bond_length_scan` | input forms ①`--from-artifact` ②`--from-job` ③`--input --coordinate` ④`--reaction` ⑤`--scan-config`; strategy ∈ {`guided_scan`, `reverse_peb`, `direct_ts`} | `run_scan`, `run_singlepoint` |
| 3 | `BatchOptimize` | `acp.workflows.batch_optimize.run_batch_optimize` | profile ∈ {`opt_only`, `opt_freq`, `opt_freq_sp`, `opt_freq_sp_thermo`}; TS vs minimum item; layout ∈ {`batch`, `single_flat`} | `run_optimize`, `run_frequency`, `run_singlepoint`, `run_thermochemistry` |
| 4 | `irc` | `acp.workflows.irc.run_irc_workflow` | direction ∈ {`forward`, `reverse`, `both`}; TS auto-detect from TAG | `run_irc` |
| 5 | `scan` | `acp.workflows.simple.run_scan` | coordinate kind ∈ {distance, angle, dihedral}; single/multi coordinate | `run_scan` |
| 6 | `tsmode` | `acp.workflows.tsmode.run_tsmode` | `--no-final-frequency` on/off; mapping verified vs `allow_unverified_mapping` | `run_optimize`, `run_frequency` |
| 7 | `casscf` | `acp.workflows.simple.run_casscf` | CASSCF only; CASSCF + SC-/FIC-NEVPT2; electronic-state routing | `run_casscf` |
| 8 | `nmr` | `acp.workflows.nmr.run_nmr_analysis` (`src/acp/workflows/nmr.py`) | preset = `censo-zero` (skip CENSO) vs non-zero preset; per-conformer GIAO (`solvent_model=none` = gas phase, no cpcm); per-conformer checkpoint + resource budget stay ACP-side | `run_conformer_search`, `run_censo_refine`, `run_nmr_shielding` |
| 9 | `singlepoint` | `acp.workflows.simple.run_singlepoint` | single-step plan (`StepKind.SINGLEPOINT`) | `run_singlepoint` |
| 10 | `optimize` | `acp.workflows.simple.run_optimize` | single-step plan (`StepKind.OPTIMIZE`) | `run_optimize` |
| 11 | `frequency` | `acp.workflows.simple.run_frequency` | single-step plan (`StepKind.FREQUENCY`) | `run_frequency` |
| 12 | `xtb_optimize` | `acp.workflows.simple.run_xtb_optimize` | `StepKind.OPTIMIZE` with `backend="xtb"` | `run_optimize` |
| 13 | `XtbPathSearch` | `acp.workflows.xtb_path.run_xtb_path_search` | frozen `pes2ts_xtb_path_request_v1` payload (verbatim recipe) | `run_xtb_path_search` |
| 14 | `OrcaGradient` | `acp.workflows.orca_gradient.run_orca_gradient` | frozen `pes2ts_orca_gradient_request_v1` payload | `run_orca_gradient` |

## Per-workflow call chains

### 1. Confsearch — 4 protocols × refinement branches

`ConfsearchEngine.run` → `_run_protocol` → one protocol module in
`acp/confsearch/protocols/`, each of which delegates (lazily) to the retained
protocol engines `acp.workflows.ensemble` / `energy` / `xtbmd_censo_energy` /
`xtbmd_md`, whose execution calls the `cccp.calculation` cores.

| Protocol | Sampling route | Legal `refinement_policy` | Task cores reached |
|----------|----------------|---------------------------|--------------------|
| `xtb-crest` | CREST screen → DFT refine | `screen`, `rank1`, `cumulative-99`, `all` | `run_conformer_search`, `run_optimize`, `run_frequency`, `run_singlepoint`, `run_thermochemistry` |
| `censo-crest` | CREST → CENSO rank → DFT refine | `screen`, `rank1`, `cumulative-99`, `all` | `run_conformer_search`, `run_censo_refine`, `run_optimize`, `run_frequency`, `run_singlepoint`, `run_thermochemistry` |
| `xtb-md` | xTB-MD sampling → ISOSTAT cluster → (xTB) | `screen` only (pure xTB) | `run_md_sampling`, `run_clustering`, `run_optimize` |
| `xtbmd-censo` | xTB-MD → ISOSTAT → CENSO → DFT refine | `screen`, `rank1`, `cumulative-99`, `all` | `run_md_sampling`, `run_clustering`, `run_censo_refine`, `run_optimize` |

Legal-branch rule (`acp/confsearch/contracts.py::validate_confsearch_request`):
`PURE_XTB_PROTOCOLS = ("xtb-crest", "xtb-md")` accept only `screen`; a non-screen
policy on a pure-xTB protocol is coerced to `screen` with a validation note
(never silently re-implemented).  Profiles (`light`/`default`/`high`) only
change quality knobs, never the sampling mechanism or the task core.

### 2. PESsearch — 5 input forms × 3 strategies

`run_pes_search` → `PesSearchEngine.run` → `run_pes_scan`
(`acp/calculations/pes/scan.py`) which:
- runs the relaxed scan via `cccp.calculation.tasks.scan.run_scan`;
- runs one cached single point per frame via
  `BatchSinglePointExecutor` → `cccp.calculation.batch.run_batch` with the
  `run_singlepoint` task core (`_singlepoint_execution.py`).

`run_bond_length_scan` (form ⑤ from `--scan-config`) takes the identical
`run_pes_scan` path.

### 3. BatchOptimize — 4 profiles

`run_batch_optimize` → `BatchOptimizeEngine.run` (`acp/calculations/batch/engine.py`).
Each profile is a superset of the previous:

| Profile | Stages | Task cores |
|---------|--------|------------|
| `opt_only` | optimize | `run_optimize` |
| `opt_freq` | + frequency | `run_optimize`, `run_frequency` |
| `opt_freq_sp` | + single point | `run_optimize`, `run_frequency`, `run_singlepoint` |
| `opt_freq_sp_thermo` | + thermochemistry | `run_optimize`, `run_frequency`, `run_singlepoint`, `run_thermochemistry` |

All four profiles dispatch through the `acp.calculations.primitives.*` compat
shims → `cccp.calculation.run_*`.  Concurrent batches additionally use
`cccp.calculation.batch.run_batch` with the same per-item cores.

### 4. irc

`run_irc_workflow` → `acp.calculations.primitives.irc.run_irc` →
`_cccp_calculation.run_irc`.  Directions `forward`/`reverse`/`both` are
resolved inside the task core; endpoint classification uses
`cccp.calculation.irc_endpoints`.

### 5. scan

`acp.workflows.simple.run_scan` → `acp.calculations.primitives.scan.run_scan`
→ `_cccp_calculation.run_scan`.

### 6. tsmode

`run_tsmode` → `TsmodeEngine.run` → `acp.calculations.primitives.optimize.run_optimize`
and `...frequency.run_frequency` → `cccp.calculation.run_optimize` /
`run_frequency`.

### 7. casscf

`acp.workflows.simple.run_casscf` builds a `CalculationPlan`
(`StepKind.CASSCF`) executed by `CalculationPlanExecutor` →
`acp.calculations.primitives.casscf.run_casscf` → `_cccp_calculation.run_casscf`.
NEVPT2 correlation is an option of the same core (no second implementation).

### 8. nmr

`run_nmr_analysis` (`src/acp/workflows/nmr.py`) → conformer stage calls the
`run_conformer_search` core, then `run_censo_refine` for every preset except
`censo-zero` (which skips CENSO), then per-conformer GIAO straight through the
unique `cccp.calculation.tasks.nmr_shielding.run_nmr_shielding` core (direct
import, `TaskContext(capability_extras={"nuclei": ...})`).  `solvent_model=none`
runs the shielding stage gas-phase with no cpcm/solvent keyword; the effective
model is recorded in the protocol/report evidence.  ACP keeps orchestration and
publication only: the per-conformer shielding checkpoint + resource budget
(plan todo 28) and the six-part protocol / ensemble-quality records (todos
29/30) live here, never a second executor.  The configured `CrestBackend`
instance is only a runtime seam injected into the core's `TaskContext`; it is
not an alternate execution path.

### 9–12. simple single-request workflows

`singlepoint` / `optimize` / `frequency` / `xtb_optimize` in
`acp/workflows/simple.py` build a one-item `CalculationPlan` and run it through
`CalculationPlanExecutor` (`acp/calculations/executor.py`).  The executor's
`_PRIMITIVE_DISPATCH` maps each `StepKind` to its compat shim, which calls
`cccp.calculation.run_singlepoint` / `run_optimize` / `run_frequency`.
`xtb_optimize` uses the same `run_optimize` core with `backend="xtb"`.

### 13. XtbPathSearch

`run_xtb_path_search` (`acp/workflows/xtb_path.py`) validates the frozen
`pes2ts_xtb_path_request_v1` payload and forwards it, via a `TaskContext`, to
`cccp.calculation.tasks.xtb_path_search.run_xtb_path_search`.  The recipe is
never defaulted in ACP.

### 14. OrcaGradient

`run_orca_gradient` (`acp/workflows/orca_gradient.py`) validates the frozen
`pes2ts_orca_gradient_request_v1` payload and forwards it to
`cccp.calculation.tasks.orca_gradient.run_orca_gradient`.  ORCA route/block
rendering stays in `cccp/qc`.

## Single execution core (A4)

- Unique primitive definitions are guarded by
  `tests/test_architecture_invariants.py::test_unique_primitive_definitions`.
- `tests/test_acp_workflow_task_matrix.py` additionally asserts that every
  workflow route references a `cccp.calculation` task core, that no executable
  `get_backend(` / `require_backend(` / `run_shermo(` call remains in
  `src/acp`, and that the only ACP-side `def run_singlepoint|run_optimize|
  run_frequency|run_thermochemistry` bodies are the sanctioned compat shims
  (`acp/calculations/primitives/*`) and the plan adapters
  (`acp/workflows/simple.py`) — never a QC implementation.

## Non-goals / notes

- `cccp.calculation` is the sole execution station; `src/acp/backends/` is a
  pure re-export shim and `src/acp/calculations/primitives/` is pure
  forwarding + ACP publication.
- Retired engines (`ensemble`, `energy`, `xtbmd_censo_energy`, `xtbmd_md`) are
  reached **only** through the Confsearch protocols above and still terminate
  in `cccp.calculation` task cores.
