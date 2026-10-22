# ACP2 Plan A — Fifteen-Task Compatibility Matrix (A-06b)

Status: static contract record. Source of truth is the code at commit `42f5272`
(parent of the A-06b delivery commit) on `acp2/a-science`.

Scope: the fifteen `cccp.calculation.requests.TaskKind` members and their
package-level export status. This matrix records **static facts only**. It makes
no claim that any task was executed on this host: every real QC launch remains
`needs-real-evidence` (`baseline.md` §8.7).

Column semantics:

- **Execution status** — every task kind here is `implemented`; no task kind in
  this matrix is `pure-analysis`. `pure-analysis` is reserved for
  `vibration.analyze`, which is a capability (`cccp.analysis.frequency_products
  .build_frequency_report`), **not** a task kind, so it has no row below.
- **Execution mechanism** — every one of the fifteen task cores reaches a QC
  executable through the capability/backend/`cccp.qc` seam (`QC-subprocess`).
  Whether a given host can launch the binary, and with what result, is
  `needs-real-evidence` and is not asserted here.
- **Reachability** — a kind is reachable through the ACP2 capability binding
  (`bind_capability`) when a registered capability declares it as
  `delegates_to_task_kind`; otherwise it is reachable only as a direct
  `TaskKind` reference (`bind_task_kind`).
- **Evidence** — every row is `static-fact`, verified by reading
  `requests.py`/`results.py`/`tasks/__init__.py`/`calculation/__init__.py` and by
  importing the package. No row claims a real run.

Inventory identity: `TaskKind` has 15 members
(`src/cccp/calculation/requests.py:54-72`), `TASK_KINDS` has 15 entries
(`src/cccp/calculation/capabilities/contracts.py:109-125`), `TASK_OPTIONS_TYPES`
has 15 rows (`requests.py:1719-1736`), `TASK_PAYLOAD_TYPES` has 15 rows
(`results.py:1309-1326`), and `cccp.calculation.tasks.__all__` exports 15
`run_*` (`tasks/__init__.py:5-37`).

| # | Task kind | `run_*` entry point | Options type | Payload type | Result type | Capability binding | Export before (package lazy) | Export after (package lazy) | Execution status | Execution mechanism | Reachable via | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `singlepoint` | `cccp.calculation.tasks.singlepoint.run_singlepoint` | `SinglePointOptions` | `SinglePointPayload` | `TaskResult` (schema v1) | `electronic.energy` | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | capability binding + direct `TaskKind` | `static-fact` |
| 2 | `optimize` | `cccp.calculation.tasks.optimize.run_optimize` | `OptimizeOptions` | `OptimizePayload` | `TaskResult` (schema v1) | `geometry.optimize` | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | capability binding + direct `TaskKind` | `static-fact` |
| 3 | `frequency` | `cccp.calculation.tasks.frequency.run_frequency` | `FrequencyOptions` | `FrequencyPayload` | `TaskResult` (schema v1) | `vibration.compute` | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | capability binding + direct `TaskKind` | `static-fact` |
| 4 | `scan` | `cccp.calculation.tasks.scan.run_scan` | `ScanOptions` | `ScanPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 5 | `irc` | `cccp.calculation.tasks.irc.run_irc` | `IrcOptions` | `IrcPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 6 | `casscf` | `cccp.calculation.tasks.casscf.run_casscf` | `CasscfOptions` | `CasscfPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 7 | `thermochemistry` | `cccp.calculation.tasks.thermochemistry.run_thermochemistry` | `ThermochemistryOptions` | `ThermochemistryPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 8 | `conformer_search` | `cccp.calculation.tasks.conformer_search.run_conformer_search` | `ConformerSearchOptions` | `ConformerSearchPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 9 | `md_sampling` | `cccp.calculation.tasks.md_sampling.run_md_sampling` | `MdSamplingOptions` | `MdSamplingPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 10 | `clustering` | `cccp.calculation.tasks.clustering.run_clustering` | `ClusteringOptions` | `ClusteringPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 11 | `censo_refine` | `cccp.calculation.tasks.censo_refine.run_censo_refine` | `CensoRefineOptions` | `CensoRefinePayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 12 | `nmr_shielding` | `cccp.calculation.tasks.nmr_shielding.run_nmr_shielding` | `NmrShieldingOptions` | `NmrShieldingPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 13 | `xtb_path_search` | `cccp.calculation.tasks.xtb_path_search.run_xtb_path_search` | `XtbPathSearchOptions` | `XtbPathSearchPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 14 | `orca_gradient` | `cccp.calculation.tasks.orca_gradient.run_orca_gradient` | `OrcaGradientOptions` | `OrcaGradientPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✓ package lazy | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |
| 15 | `xtb_constrained_opt` | `cccp.calculation.tasks.xtb_constrained_opt.run_xtb_constrained_opt` | `XtbConstrainedOptOptions` | `XtbConstrainedOptPayload` | `TaskResult` (schema v1) | none | ✓ tasks / ✗ package lazy (GAP-8) | ✓ tasks / ✓ package lazy | `implemented` | `QC-subprocess` | direct `TaskKind` only | `static-fact` |

## Direct-task-kind reachability

All fifteen kinds are accepted by `TaskKindReference` / `bind_task_kind`
(`src/cccp/calculation/capabilities/task_binding.py`), so a recipe step can name
any of them without inventing a capability. Only rows 1–3 are additionally
reachable through a capability (`bind_capability`). For the other twelve,
`TaskBinding.execute()` raises the typed `UnsupportedBindingError` rather than
inventing an execution route.

## Export-gap repair (GAP-8)

Before A-06b, `cccp.calculation.run_xtb_constrained_opt`,
`cccp.calculation.XtbConstrainedOptOptions` and
`cccp.calculation.XtbConstrainedOptPayload` raised `AttributeError` even though
`cccp.calculation.tasks` exported the runner and `requests`/`results` defined the
types. A-06b adds exactly those three names to `_LAZY_EXPORTS` in
`src/cccp/calculation/__init__.py`; no existing export was renamed or removed,
and no task behavior changed. `tests/test_acp2_science_task_binding.py` now
asserts importability, type identity, and that every `TASK_KINDS` entry has a
`run_*` export pointing at `tasks.<kind>`.

## Non-claims

- No task was launched for this matrix. `QC-subprocess` describes the static
  mechanism (task core → capability/backend → `cccp.qc`), not an observed run.
- The matrix does not assert binary availability, licence state, LSF eligibility
  or numerical output. Those remain `needs-real-evidence` (`baseline.md` §8.7).
