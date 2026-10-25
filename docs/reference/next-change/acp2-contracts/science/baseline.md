# ACP2 Plan A — Science Baseline Record (A-00 / B00)

Recorded: 2026-10-22 (UTC run `a00-20261022T111022Z`)
Owner: A (science track). This file is the single baseline record consumed by tasks A-01…A-29.

## 1. Revisions

| Field | Value |
|---|---|
| Actual worktree HEAD at A-00 start | `096cdffe9a3817d8ee443d824830d87ec6e07bc2` (bare base, no business fixes) |
| Branch | `acp2/a-science` (worktree `/opt/acp2-dev/A`) |
| Frozen base revision (`ACP2_BASE_REV`) | `096cdffe9a3817d8ee443d824830d87ec6e07bc2` — overlay restore/apply base only; **not** the code candidate |
| B00 candidate id (`candidate-snapshot.json::candidate_id`) | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` |
| Code-candidate revision (declared in repair report, historical reference) | `ab492cc8be9e46125fa2d2fae69a3ecaac64a548` |
| **Delivery revision** (baseline commit created by A-00) | **`2e0ce9c202f5a65180aa97ca2787f82dc0054e6b`** |
| Commit subject | `chore(cccp): lock ACP2 science baseline and ownership boundary` |

`096cdff` is the Git starting point only. The executed code is the overlay-restored candidate; `2e0ce9c` is the commit that materializes it on `acp2/a-science`.

## 2. B00 overlay (authoritative source of truth)

| Field | Value |
|---|---|
| Overlay archive (concrete path) | `reports/architecture_repair_20261022/candidate-overlay.tar.gz` |
| Overlay archive SHA-256 | `3fb835b5069f96642bf6bb889a804532bbb70b69cf796d21208a6e3364117734` (matches `SHA256SUMS`) |
| `tracked-changes.patch` SHA-256 | `4578405dc1b99d9a884c651a8ea064a6eed311128b9564966200b33e95aa9242` |
| Snapshot-declared `tracked_patch_sha256` | `4578405dc1b99d9a884c651a8ea064a6eed311128b9564966200b33e95aa9242` (**match**) |
| `candidate-snapshot.json` SHA-256 | `37dd2320cc5d97ea7eb866a2275641c0d0518801202137f08e8f84046cea1194` |
| Patch content | 28 tracked files (`git apply` to `096cdff`) |
| New files | 144 files (`new-files/` copied over the worktree) |
| Excluded path | `tests/baseline/refactor-evidence/e2e-task-root.txt` — pre-existing local test-run marker; **not** committed, **not** modified |

Materialization commands (executed in `/opt/acp2-dev/A`):

```bash
tar -xzf reports/architecture_repair_20261022/candidate-overlay.tar.gz -C /tmp/opencode/a00-overlay-a00-20261022T111022Z
git apply /tmp/opencode/a00-overlay-a00-20261022T111022Z/tracked-changes.patch
cp -a /tmp/opencode/a00-overlay-a00-20261022T111022Z/new-files/. .
```

## 3. Snapshot verification

Every path in `candidate-snapshot.json::files` was re-hashed from the materialized worktree:

| Metric | Result |
|---|---|
| Files verified | **1906 / 1906** |
| Drift | **0** |
| Missing | 0 |
| Result | **PASS** |

Raw machine record: `$ACP2_EVIDENCE/A/A-00/a00-20261022T111022Z/snapshot-verify.json`.

## 4. Interpreter and import resolution

| Field | Value |
|---|---|
| Role interpreter (`$ACP2_A_PYTHON`) | `/opt/acp2-dev/envs/a-python/bin/python` (CPython 3.11.13) |
| Editable install | `"$ACP2_A_PYTHON" -m pip install -e "$ACP2_A_SOURCE" --no-deps --no-build-isolation` (exit 0) |
| `cccp` import | `/opt/acp2-dev/A/src/cccp/__init__.py` |
| `acp` import | `/opt/acp2-dev/A/src/acp/__init__.py` |
| `acp_services` import | `/opt/acp2-dev/A/src/acp_services/__init__.py` |
| All imports in source tree | **YES** (all three resolve under `/opt/acp2-dev/A/src/`) |

Build note: the venv has no `wheel` distribution, so the first editable build failed at
`error: invalid command 'bdist_wheel'`. The build was re-run with the already-present local
build environment on `PYTHONPATH`
(`/tmp/acp-repair-build-env/lib/python3.11/site-packages`, wheel 0.48.0); no network install and
no new runtime package was added.

## 5. Evidence root

`$ACP2_EVIDENCE` = `/opt/acp2-evidence/a-science-20261022`

A-00 evidence: `/opt/acp2-evidence/a-science-20261022/A/A-00/a00-20261022T111022Z/`
(`result.json`, `argv.json`, `stdout.log`, `stderr.log`, `pre-state.txt`,
`snapshot-verify.json`, `overlay-digests.txt`, `failure-qa.txt`).

## 6. Dirty-path inventory before materialization

### 6a. A worktree (`/opt/acp2-dev/A`)

Command: `cd "$ACP2_A_SOURCE" && git status --short && git diff --stat`
(at HEAD `096cdffe9a3817d8ee443d824830d87ec6e07bc2`, branch `acp2/a-science`):

```text
<no output>   # git status --short returned 0 lines
<no output>   # git diff --stat returned no changes
```

Inventory: **0 dirty paths** (clean worktree, no staged/unstaged/untracked entries). The A worktree
was created fresh from the bare base; no other agent had written to it.

### 6b. Primary checkout (`repo-root`)

GAP-13 names the dirty primary checkout and its untracked planning documents as the baseline risk.
Observed by the orchestrator at **2026-10-22T11:06Z**, before A-00 materialized the overlay.
Branch `feat/frontend-plan1-closure`, HEAD `096cdffe9a3817d8ee443d824830d87ec6e07bc2`.
The primary checkout was **only read** by A-00 (overlay tarball, `REPORT.md`, `SHA256SUMS`, evidence
JSON and the cited plan documents); nothing was cleaned, reset, stashed, committed or otherwise written.

`git -C repo-root status --short` — **39 modified + 11 untracked**:

```text
M README.md
M docs/ACP_Architecture_Adjustment_Plan_20261016.md
M frontend/js/module_tests/nmr_viewer.test.js
M frontend/js/workbench/features/nmr.js
M pyproject.toml
M scripts/acceptance/four_combinations.py
M scripts/install_systemd.sh
M src/acp/api/server.py
M src/acp/calculations/attempt_freeze.py
M src/acp/calculations/attempt_publication.py
M src/acp/calculations/batch/options.py
M src/acp/cli.py
M src/acp/init_wizard/answers.py
M src/acp/init_wizard/flows.py
M src/acp/init_wizard/plan.py
M src/acp/scheduler/manager.py
M src/acp/scheduler/remote/runner.py
M src/acp/services_data_bootstrap.py
M src/acp_services/data/operations.py
M src/acp_services/data/read_root.py
M src/acp_services/runtime/attempt_freeze.py
M src/acp_services/runtime/isolation.py
M src/acp_services/runtime/worker.py
M src/acp_services/worker_bootstrap.py
M src/acp_services/workspace/boundary.py
M src/cccp/analysis/bounded/identity.py
M src/cccp/analysis/bounded/manifest.py
M src/cccp/analysis/bounded/multiresolution.py
M tests/baseline/refactor-evidence/e2e-task-root.txt
M tests/baseline/services-evidence/migration_ledger.md
M tests/baseline/services-evidence/packaging_rollout_receipts.md
M tests/baseline/services-evidence/public_shim_inventory.md
M tests/test_acp_frontend_browser.py
M tests/test_acp_init_cli.py
M tests/test_acp_wizard_browser.py
M tests/test_services_chain_switch.py
M tests/test_services_init_unified.py
M tests/test_services_local_boundary.py
M tests/test_services_worker_bootstrap.py
?? docs/ACP_2.0_Four_Component_Collaboration_Plan_20261020.md
?? docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md
?? docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plan_B_Runtime_20261020.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R2_20261021.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R3_20261021.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R4_20261022.md
?? docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_R5_20261022.md
```

### 6c. Mapping to the reviewed candidate (`tracked-changes.patch`)

All **28** files in `tracked-changes.patch` are present in the 39 modified paths (**28 inside**,
**11 outside**). Set difference computed from `git apply --numstat tracked-changes.patch`.

| Category of the 11 outside paths | Paths | Report classification |
|---|---|---|
| Docs | `README.md` | Outside reviewed candidate; `REPORT.md` does not claim it |
| Scripts / ops | `scripts/install_systemd.sh` | Outside reviewed candidate |
| ACP product code | `src/acp/cli.py`, `src/acp/init_wizard/answers.py`, `src/acp/init_wizard/flows.py`, `src/acp/init_wizard/plan.py` | Outside reviewed candidate (B-owned surface) |
| ACP Services product code | `src/acp_services/workspace/boundary.py` | Outside reviewed candidate (B-owned surface) |
| Tests | `tests/test_acp_init_cli.py`, `tests/test_services_init_unified.py`, `tests/test_services_local_boundary.py` | Outside reviewed candidate |
| Excluded baseline marker | `tests/baseline/refactor-evidence/e2e-task-root.txt` | **Explicitly excluded** local test-run marker (snapshot `excluded`), never committed |

Exact list of the 11 modified paths **not** covered by the overlay:

```text
README.md
scripts/install_systemd.sh
src/acp/cli.py
src/acp/init_wizard/answers.py
src/acp/init_wizard/flows.py
src/acp/init_wizard/plan.py
src/acp_services/workspace/boundary.py
tests/baseline/refactor-evidence/e2e-task-root.txt
tests/test_acp_init_cli.py
tests/test_services_init_unified.py
tests/test_services_local_boundary.py
```

`REPORT.md` defines the candidate as the overlay of previously-uncommitted debug changes plus this
round's fixes; these 11 paths are **not** part of that candidate and therefore remain uncommitted
work-in-progress in the primary checkout (the GAP-13 risk). A-00 does not copy, merge, revert or
"fix" them.

The **11 untracked planning documents** (all under `docs/`, listed above) are **not part of the code
candidate**. They are recorded here for completeness only: they document the ACP 2.0 parallel plan,
the MCP/DSH runbook, the execution index and the plan reviews. `tests/baseline/refactor-evidence/e2e-task-root.txt`
is explicitly excluded and stays untouched in both the primary checkout and the A worktree.

## 7. Cross-references

- Plan: `docs/reference/next-change/ACP_2.0_Parallel_Plan_A_CCCP_20261020.md`
- Index: `docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md`
- Repair report: `reports/architecture_repair_20261022/REPORT.md`
- Deliverable path map / ownership boundary: `path-map.md` (same directory)

---

## 8. A-00b frozen inventory — workflows, task kinds, contracts, compatibility surface

Recorded by task **A-00b** on 2026-10-22 (UTC run `a00b-20261022T121442Z`) from the A worktree at
`acp2/a-science` (delivery `2e0ce9c`, docs `7f4944e` / `94c8ea9`). Every count below was read from
code and re-executed under `$ACP2_A_PYTHON` (import-level introspection + the in-process API
response builder); no QC executable was launched by this task.

Evidence markers used in every row of §8.1–§8.9:

| Marker | Meaning |
|---|---|
| `static-fact` | Proven by source reading and/or import-level introspection executed in this run. Implies no QC execution and no live-service round trip. |
| `needs-real-evidence` | Requires a real computation, a live service, another track's published artifact, or a clean-environment install. Stays `NOT_VERIFIED` until that evidence exists (see §8.7 and `cross-track-dependencies.json`). |

### 8.1 The fifteen active workflows

Count proof (all executed in this run):

| Proof step | Result |
|---|---|
| `acp.catalog.WORKFLOW_CATALOG` status counts | **15 `active`**, 14 `retired`, 1 `planned` (`custom_sequence`) — 30 rows total |
| `acp.scheduler.jobs.PUBLIC_WORKFLOWS` (`jobs.py:106-136`, derived from catalog `status=="active"`) | 15 ids, catalog order |
| `acp.scheduler.jobs.ALL_WORKFLOWS` / `SUPPORTED_WORKFLOWS` | 15 + synthetic `fake` = 16 |
| `acp.workflows.registry._WORKFLOW_REGISTRY` (`registry.py:31-156`) | 16 keys (15 + `fake`) |
| `acp.workflows.registry.list_workflow_entries()` (`registry.py:159-169`, filters `PUBLIC_WORKFLOWS`) | **15** entries |
| `tests/test_architecture_invariants.py:91-112` (`len == 15` + exact set) and `:115-120` (order) | pass (guard suite green in this run) |
| `acp.api.routes._build_workflow_info()` executed in-process (the function `GET /api/workflows` calls via `routes.py:237` / `v1_routes.py:1123`) | **15** `WorkflowInfo` records — see §8.8 for the README "14" claim |

| # | CLI name (`acp run …`) | Registry key (catalog id = `WorkflowRegistryEntry.name`) | `WorkflowSpec` / execution vehicle | Mapping to task kind(s) — or explicit not-a-task-kind reason | Name mismatch called out | Evidence |
|---|---|---|---|---|---|---|
| 1 | `singlepoint` | `singlepoint` (`registry.py:104`; catalog active; CLI `_handle_singlepoint` `cli.py:3942`) | No `WorkflowSpec(...)` constructed; vehicle = `workflows/simple.py:576 run_singlepoint` → `plans.py:78 build_simple_plan(StepKind.SINGLEPOINT)` → `CalculationPlanExecutor` → `primitives.singlepoint.run_singlepoint` (pure forwarder) | → task kind **`singlepoint`** (1:1) | none (CLI id == task id) | `static-fact` |
| 2 | `optimize` | `optimize` (`registry.py:110`; `_handle_optimize` `cli.py:3983`) | `simple.py:625 run_optimize` → `StepKind.OPTIMIZE` plan → executor | → task kind **`optimize`** (1:1) | none | `static-fact` |
| 3 | `frequency` | `frequency` (`registry.py:116`; `_handle_frequency` `cli.py:4021`) | `simple.py:673 run_frequency` → `StepKind.FREQUENCY` plan → executor | → task kind **`frequency`** (1:1) | none | `static-fact` |
| 4 | `scan` | `scan` (`registry.py:122`; `_handle_scan` `cli.py:4059`) | `simple.py:697 run_scan` → `primitives.scan.run_scan` (alias of `execute_scan`) | → task kind **`scan`** (1:1) | none | `static-fact` |
| 5 | `irc` | `irc` (`registry.py:128`; `_handle_irc` `cli.py:4150`) | `workflows/irc.py:39 run_irc_workflow` → `primitives.irc.run_irc` (alias of `execute_irc`) | → task kind **`irc`** (1:1) | none | `static-fact` |
| 6 | `tsmode` | `tsmode` (`registry.py:134`; `_handle_tsmode` `cli.py:4227`) | `workflows/tsmode.py:125 run_tsmode` → `calculations/tsmode/engine.py TsmodeEngine` (uses `primitives.optimize.run_optimize` at `engine.py:849`, `primitives.frequency.run_frequency` at `engine.py:883`) | → task kinds **`optimize`** (directed OptTS stage) **+ `frequency`** (verification stage). **Not a task kind:** there is no `TaskKind.TSMODE`/`run_tsmode`; tsmode is a workflow-specific engine composing two task cores. | **mismatch**: workflow `tsmode` has no same-named task kind | `static-fact` |
| 7 | `casscf` | `casscf` (`registry.py:144`; `_handle_casscf` `cli.py:3892`) | `simple.py:600 run_casscf` → `StepKind.CASSCF` plan → executor | → task kind **`casscf`** (1:1) | none | `static-fact` |
| 8 | `xtb_optimize` | `xtb_optimize` (`registry.py:150`; `_handle_xtb_optimize` `cli.py:4299`) | `simple.py:649 run_xtb_optimize` → `build_simple_plan(StepKind.OPTIMIZE, backend="xtb")` → executor | → task kind **`optimize`** (backend `xtb`) | **mismatch**: workflow `xtb_optimize` ≠ task `optimize` — the workflow pins the *backend*, it is not a distinct task kind | `static-fact` |
| 9 | `nmr` | `nmr` (`registry.py:95`; `_handle_nmr` `cli.py:4485`) | `workflows/nmr.py:3243 run_nmr_analysis` → `_run_conformer_tasks` / `_run_giao_for_conformers` | → task kinds **`conformer_search` + `censo_refine` + `nmr_shielding`** (three task cores driven by one workflow; `nmr.py:150-152`) | **mismatch + multi-task**: workflow `nmr` ≠ task `nmr_shielding` alone; DP4/DP5 reporting is ACP-side analysis, not a task kind | `static-fact` |
| 10 | `Confsearch` | `Confsearch` (`registry.py:38`; `_handle_confsearch` `cli.py:1281`) | `acp.confsearch.ConfsearchEngine` + `CONFSEARCH_STAGES` → `protocols/__init__.py:31 PROTOCOL_RUNNERS` (`xtb-crest`, `xtb-md`, `censo-crest`, `xtbmd-censo`) → legacy internal engines `workflows/ensemble.py run_ensemble_generation`, `workflows/energy.py run_conformer_energy`, `workflows/xtbmd_md.py run_md_replicas`, `workflows/xtbmd_censo_energy.py run_xtbmd_censo_energy` | → task kinds **`conformer_search`, `censo_refine`, `thermochemistry`, `optimize`, `frequency`, `singlepoint`** (via `workflows/energy_shared.py:64-69` → task cores) and **`md_sampling`, `clustering`** (via `calculation/xtbmd_adapters.py:51-53`). **Not a task kind:** Confsearch is a four-protocol orchestrator; there is no `TaskKind.CONFSEARCH`/`run_confsearch`. | **mismatch**: `Confsearch` (PascalCase, orchestration) vs task `conformer_search` (snake_case, one core); retired engines are protocol engines only (CLI-blocked as workflows) | `static-fact` |
| 11 | `PESsearch` | `PESsearch` (`registry.py:47`; `_handle_pessearch` `cli.py:1371`) | `workflows/pes_search.py:97 run_pes_search` / `:196 run_bond_length_scan` → `calculations/pes/scan.py:656 run_pes_scan` → `cccp` task core `tasks.scan.run_scan` + ACP-side PES candidate/TS extraction outputs | → task kind **`scan`** plus ACP-side planning/extraction products. **Not a task kind:** no `TaskKind.PESSEARCH`; PESsearch plans and post-processes scans. | **mismatch**: `PESsearch` ≠ `scan` | `static-fact` |
| 12 | `BatchOptimize` | `BatchOptimize` (`registry.py:56`; `_handle_batch_optimize` `cli.py:1830`) | `workflows/batch_optimize.py:110 run_batch_optimize` → `calculations/batch/engine.py BatchOptimizeEngine` → per item `run_optimize` (`engine.py:1113/1115`), `run_frequency` (`:1210`), `run_singlepoint` (`:1244`) | → task kinds **`optimize` + `frequency` + `singlepoint`** (profile-driven, plus thermochemistry through the shared primitives). **Explicitly not one task kind:** this is the multi-task batch engine. | **mismatch / multi-task**: `BatchOptimize` is a multi-task engine, never a single task kind | `static-fact` |
| 13 | `XtbPathSearch` | `XtbPathSearch` (`registry.py:65`; `_handle_xtb_path_search` `cli.py:1978`) | `workflows/xtb_path.py:634 run_xtb_path_search` → `calculations/legacy_adapters.pes2ts_xtb_path_to_task_request` → task core | → task kind **`xtb_path_search`** (1:1, consumes frozen `pes2ts_xtb_path_request_v1`) | case-only mismatch (workflow `XtbPathSearch` vs task `xtb_path_search`) | `static-fact` |
| 14 | `OrcaGradient` | `OrcaGradient` (`registry.py:85`; `_handle_orca_gradient` `cli.py:2787`) | `workflows/orca_gradient.py:500 run_orca_gradient` → `legacy_adapters.pes2ts_orca_gradient_to_task_request` → task core | → task kind **`orca_gradient`** (1:1, frozen `pes2ts_orca_gradient_request_v1`) | case-only mismatch | `static-fact` |
| 15 | `XtbConstrainedOpt` | `XtbConstrainedOpt` (`registry.py:74`; `_handle_xtb_constrained_opt` `cli.py:2039`) | `workflows/xtb_constrained_opt.py:547 run_xtb_constrained_opt` → `legacy_adapters.pes2ts_xtb_constrained_opt_to_task_request` → task core | → task kind **`xtb_constrained_opt`** (1:1, frozen `pes2ts_xtb_constrained_opt_request_v1`) | case-only mismatch | `static-fact` |

**`WorkflowSpec` reality check (name mismatch called out):** `acp.core.workflow.WorkflowSpec`
(`src/acp/core/workflow.py:30`) and `WorkflowRunner` exist, but **zero** active workflows construct a
`WorkflowSpec` — `grep -rn "WorkflowSpec(" src/ tests/` hits only `core/workflow.py` and
`tests/test_core_workflow.py`. The live dispatch vehicle is the CLI handler → workflow function →
(`CalculationPlan`/`CalculationPlanExecutor` | workflow-specific engine | frozen `pes2ts_*` payload →
task core), and the workflows return `WorkflowResult` / `CalculationResult`. The nested
`src/acp/workflows/AGENTS.md` description "registry maps CLI subcommand → `WorkflowSpec` builder" is
a stale label: `acp.workflows.registry` is a **metadata** registry (`WorkflowRegistryEntry`), not a
spec-builder map. Recorded as doc drift, not corrected here (AGENTS files are B-owned).

Synthetic/non-public: `fake` is registered (`registry.py:32`) but excluded from
`PUBLIC_WORKFLOWS`/`list_workflow_entries()`; the retired CLI ids are intercepted by
`_CLI_REMOVED_WORKFLOWS` (`cli.py:4433`).

### 8.2 The fifteen task kinds

Count proof: `TaskKind` has **15** members (`requests.py:54-72`); `cccp.calculation.tasks.__all__`
exports **15** `run_*` (`tasks/__init__.py:5-37`); `TASK_OPTIONS_TYPES` **15** rows
(`requests.py:1719-1736`); `TASK_PAYLOAD_TYPES` **15** rows (`results.py:1311-1328`);
`TASK_CAPABILITY_MAP` **15** rows (`backends/matrix.py:250-266`). (Stale comment: `requests.py:55`
still says "seven core + seven P2" = 14 while the enum carries 15 — see §8.9.)

| # | Task kind id | `run_*` entry point | Options type | Payload (result) type | Result envelope | Exported? | Starts a QC subprocess (default path, mechanism) | Evidence |
|---|---|---|---|---|---|---|---|---|
| 1 | `singlepoint` | `cccp.calculation.tasks.singlepoint.run_singlepoint` | `SinglePointOptions` | `SinglePointPayload` | `TaskResult` (schema v1) | tasks ✓ (`tasks/__init__.py:16`); package lazy ✓ (`calculation/__init__.py:450`) | yes — ORCA/xTB capability → `cccp.qc.interfaces` subprocess layer (backends themselves hold no subprocess) | `static-fact` |
| 2 | `optimize` | `cccp.calculation.tasks.optimize.run_optimize` | `OptimizeOptions` | `OptimizePayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — ORCA/xTB | `static-fact` |
| 3 | `frequency` | `cccp.calculation.tasks.frequency.run_frequency` | `FrequencyOptions` | `FrequencyPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — ORCA | `static-fact` |
| 4 | `scan` | `cccp.calculation.tasks.scan.run_scan` | `ScanOptions` | `ScanPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ (`:449`) | yes — ORCA/xTB | `static-fact` |
| 5 | `irc` | `cccp.calculation.tasks.irc.run_irc` | `IrcOptions` | `IrcPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — ORCA | `static-fact` |
| 6 | `casscf` | `cccp.calculation.tasks.casscf.run_casscf` | `CasscfOptions` | `CasscfPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — ORCA (CASSCF/NEVPT2) | `static-fact` |
| 7 | `thermochemistry` | `cccp.calculation.tasks.thermochemistry.run_thermochemistry` | `ThermochemistryOptions` | `ThermochemistryPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — exactly one Shermo launch per request via `cccp.qc.shermo_adapter.execute_shermo` (`tasks/thermochemistry.py:40,99`) | `static-fact` |
| 8 | `conformer_search` | `cccp.calculation.tasks.conformer_search.run_conformer_search` | `ConformerSearchOptions` | `ConformerSearchPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — CREST/CENSO/Molclus (priority `crest → censo → molclus`, `matrix.py:290`) | `static-fact` |
| 9 | `md_sampling` | `cccp.calculation.tasks.md_sampling.run_md_sampling` | `MdSamplingOptions` | `MdSamplingPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — Molclus (xTB-MD) | `static-fact` |
| 10 | `clustering` | `cccp.calculation.tasks.clustering.run_clustering` | `ClusteringOptions` | `ClusteringPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — Isostat/external `cluster` capability (`tasks/clustering.py:126` → `cccp.qc.interfaces.isostat`, `subprocess.run` at `interfaces/isostat.py:231`); in-process RMSD use (`:330`) is post-processing only | `static-fact` |
| 11 | `censo_refine` | `cccp.calculation.tasks.censo_refine.run_censo_refine` | `CensoRefineOptions` | `CensoRefinePayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — CENSO | `static-fact` |
| 12 | `nmr_shielding` | `cccp.calculation.tasks.nmr_shielding.run_nmr_shielding` | `NmrShieldingOptions` | `NmrShieldingPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — ORCA GIAO | `static-fact` |
| 13 | `xtb_path_search` | `cccp.calculation.tasks.xtb_path_search.run_xtb_path_search` | `XtbPathSearchOptions` | `XtbPathSearchPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — xTB | `static-fact` |
| 14 | `orca_gradient` | `cccp.calculation.tasks.orca_gradient.run_orca_gradient` | `OrcaGradientOptions` | `OrcaGradientPayload` | `TaskResult` (v1) | tasks ✓; package lazy ✓ | yes — ORCA EnGrad | `static-fact` |
| 15 | `xtb_constrained_opt` | `cccp.calculation.tasks.xtb_constrained_opt.run_xtb_constrained_opt` | `XtbConstrainedOptOptions` | `XtbConstrainedOptPayload` | `TaskResult` (v1) | tasks ✓ (`tasks/__init__.py:18`) **but NOT package-level lazy** — `run_xtb_constrained_opt` is absent from `_LAZY_EXPORTS` (`calculation/__init__.py:439-452`) and from the `TYPE_CHECKING` block (`:284-320`, which also omits `run_scan`); runtime-checked in this run: `getattr(cccp.calculation, "run_xtb_constrained_opt")` → `AttributeError` = **GAP-8**, repaired by A-06b (todo 14) | yes — xTB | `static-fact` (export gap runtime-verified); real xTB execution `needs-real-evidence` |

Subprocess column notes (apply to all 15): the *mechanism* is static (`cccp.calculation.tasks` →
`selection` → `cccp.backends` capability → `cccp.qc.interfaces`/runners launch the executable);
whether a given host can actually launch it (binary present, licence, LSF window) is
`needs-real-evidence` (§8.7). Injecting `TaskContext.backend` bypasses the launch by design (used by
`scripts/acceptance/release_role_probe.py::science_probe`), and selection/precheck failures exit
before any subprocess.

### 8.3 Request / result / context contracts

| Contract | Location + version | Serialization rule | Evidence |
|---|---|---|---|
| `TaskRequest` (serializable intent) | `src/cccp/calculation/requests.py:1762`; `TASK_REQUEST_SCHEMA_VERSION = 1` (`requests.py:51`) | JSON-safe: task, structure (`StructureInput` `:139`), level (`MethodSpec` `:79`), options, resources (`:228`), electronic state, output dir; strict `from_dict`/`to_dict` helpers | `static-fact` |
| Typed options union | `TASK_OPTIONS_TYPES` (`requests.py:1719`), 15 rows | one options class per `TaskKind`; unknown/mismatched option payload → `TaskInputError` | `static-fact` |
| `TaskResult` (typed envelope) | `src/cccp/calculation/results.py:1387`; `TASK_RESULT_SCHEMA_VERSION = 1` (`results.py:38`); `ErrorKind` (`:41`) | JSON-safe envelope + `TASK_PAYLOAD_TYPES` (15 rows, `results.py:1311`); artifacts ser/de `artifact_ref_to_dict/from_dict` | `static-fact` |
| `TaskContext` (runtime companion) | `src/cccp/calculation/context.py:33-107`, `resolve_context` (`:109`) | **NEVER serialized** — docstring `context.py:36`: "Never serialised." Carries `config`, `workdir`, `input_base`, `progress`, `timeout_s`, `cancelled`, `backend`, `capability_extras`. `task-binding.schema.json` (A-01a, todo 3) must reference request/result schema v1 and contain **no** `TaskContext` field. | `static-fact` |
| Science products contract | `src/cccp/calculation/products/contracts.py`: `PRODUCT_SCHEMA_VERSION = 1` (`:109`), `LEGACY_PRODUCT_SCHEMA_VERSION = 0` (`:111`), `ProductKind`/`ProductState`, `ScienceProduct` (`:1327`) | product payloads versioned; legacy schema 0 read-compat preserved | `static-fact` |
| Plugin metadata contract | `src/cccp/plugin/metadata.py:155-242`, `PLUGIN_API_VERSION = 1` (`:24`) | frozen dataclass, `to_dict()` JSON-safe; construction never imports plugin code | `static-fact` |
| ACP-side legacy envelope | `acp.calculations.contracts.CalculationRequest/CalculationResult` + `legacy_adapters.to_task_request/to_legacy_result` | converted at the compatibility boundary only; no second envelope definition | `static-fact` |

### 8.4 Capability matrix and selection seam

| Fact | Value | Evidence |
|---|---|---|
| `CAPABILITY_MATRIX` (`src/cccp/backends/matrix.py:82-241`) | 7 backends (`censo`, `orca`, `crest`, `xtb`, `external`, `molclus`, `isostat`) × 20 capabilities = **140 cells**: **26 `AVAILABLE`**, **112 `NOT_IMPLEMENTED`**, **2 `STUBBED`** (crest `geometry_optimization`, crest `single_point`) | `static-fact` (counted in this run) |
| `TASK_CAPABILITY_MAP` (`matrix.py:250-266`) | **15** task rows declaring each task's full capability vocabulary | `static-fact` |
| `CAPABILITY_BACKEND_PRIORITY` (`matrix.py:277-297`) | 19 deterministic priority rows (e.g. `single_point: orca → xtb`, `conformer_search: crest → censo → molclus`) | `static-fact` |
| `CAPABILITY_ALIASES` + `normalize_capability_name` (`matrix.py:300`) | 38 aliases folded onto canonical keys; unknown name → `ValueError` → `UnsupportedCapabilityError` | `static-fact` |
| Selection seam (two-step) | `selection.py:188 capability_requirement(request)` derives the capability **from the scientific options** (structure role, optimization mode, scan constraint count, NEVPT2) → `select_semantic` (declaration-level) → `precheck_runtime` (binary availability, `selection.py`) → `select_backend`; execution dispatch stays in `cccp.calculation._common.backend_for_request` (`:394`) + `call_capability` (`:435`) | `static-fact` |
| Second-dispatch guard | no backend-level subprocess and no second `run_*` dispatch path: `tests/test_cccp_isolation.py`, `tests/test_architecture_invariants.py` (green in this run) | `static-fact` (guard), real backends `needs-real-evidence` |

### 8.5 Plugin layer (as-is)

| Item | State | Evidence |
|---|---|---|
| Modules | `src/cccp/plugin/{metadata,discovery,host,sdk,errors,example_xyz,readonly_parser}.py` — existing seam, extended (never cloned) by A-21…A-23 | `static-fact` |
| `PluginMetadata` (`metadata.py:155-242`) | `name/distribution/version/api/capabilities/science_software/resources/license/dependencies/error_semantics/default_enabled`; license must be non-empty; ≥1 unique capability; `never_overrides_builtin` forced `True` | `static-fact` |
| `PluginCapability` (`metadata.py:110-123`) | **only** `name`, `schema`, `schema_version` — no capability kind, implementation id, recipe/validator reference, trust level, distribution digest or verified-evidence field | `static-fact` = **GAP-7** (A-21a) |
| Discovery (`discovery.py:110 discover_plugins`) | metadata-only: "The candidate `loader` is never invoked here"; name/capability conflict + API-range rejection | `static-fact` |
| Entry-point group `cccp.science_plugins` | **absent** — `grep -rn "science_plugins" src/ tests/` returns 0 hits; no `importlib.metadata.entry_points` scan yet | `static-fact` = GAP-7 (A-21a adds it) |
| Sandbox / persistence | in-process host, in-memory enablement; **not** a sandbox | `static-fact` (explicit non-claim; sandbox claims `needs-real-evidence` and are out of scope) |

### 8.6 Scientific products and compatibility forwarders

| Surface | Contents | Evidence |
|---|---|---|
| Task-layer products | `src/cccp/calculation/products/{contracts,registry,serialize,curve,derived,structure,vibration}.py` — `ScienceProduct`, `PRODUCT_SCHEMA_VERSION=1`, `product_spec()/product_specs()` | `static-fact` |
| Analysis products (consumed, never re-implemented) | `src/cccp/analysis/{structure,pes,frequency_products,nmr,bounded,ensemble,sampling,tsmode,xtbmd,confsearch,identity.py,orca_parser.py}`; bounded = `{identity,manifest,multiresolution}.py`; NMR model assets under `analysis/nmr/model_assets/` (see packaging-preview.md for licence obligations) | `static-fact` |
| `acp.calculations.primitives` | pure compatibility forwarders → `cccp.calculation.tasks` (`singlepoint.py:36`, `optimize.py:129`, `frequency.py:40`, `scan.py:138 run_scan = execute_scan`, `irc.py:159 run_irc = execute_irc`, `casscf.py:39`, `thermochemistry`); uniqueness guarded by `tests/test_architecture_invariants.py:471 test_unique_primitive_definitions` (green) | `static-fact` |
| `acp.backends` | pure re-export shim with `A is B` identity (`src/acp/backends/__init__.py`), plus the quarantined legacy `batch` surface (`legacy_batch_quarantine` gate); implementations live in `cccp.backends` | `static-fact` |
| `acp.compat.legacy` | read-only historical readers: `batch_loaders.py`, `layouts.py`, `manifests.py` — never written by A | `static-fact` |
| Services identity forwarders | `acp.scheduler.{store,migrations}`, `acp.calculations.{contracts,step_result,step_requirements}`, `acp.catalog.method_levels_to_cli_flags` are `is`-identity forwarders to `acp_services` (root AGENTS ANTI #30) — A must not re-implement on the `acp` side | `static-fact` |
| Retired workflow engines as protocol engines | `workflows/{ensemble,energy,energy_shared,xtbmd_censo_energy,xtbmd_md}.py` remain lazy-loaded engines behind Confsearch protocols although their CLI ids are retired — kept on purpose (ANTI #31) | `static-fact` |

### 8.7 Rows that need later real evidence (`needs-real-evidence`)

| Row | What must happen | Owner task | State now |
|---|---|---|---|
| Real singlepoint / optimize / frequency execution on this host | run the three small-molecule scenarios with a real QC binary | A-09…A-11 (todos 19, 21, 23) | `NOT_VERIFIED` |
| Local + LSF artifact reconciliation | authorized manifests + LSF window | A-12b/A-13 (todos 25, 26) | `NOT_VERIFIED` |
| Diels-Alder chain (TS + imaginary mode, bidirectional IRC, comparable energies, search) | real DA run under frozen budgets | A-17…A-20 (todos 33-37) | `NOT_VERIFIED` |
| All 18 cross-track ledger rows except the B00 overlay | see `cross-track-dependencies.json` (`evidence_state`) | B/C producers, A consumers | `NOT_VERIFIED` |
| Minimal wheel smoke (argv recorded in `packaging-preview.md`) | `scripts/build_release.py --role cccp` in a build env that has `setuptools>=65` + `wheel` (absent from `$ACP2_A_PYTHON`; A-00a borrowed `/tmp/acp-repair-build-env` on `PYTHONPATH`, zero network) | A-27a/A-27b (todos 48, 49) | preview only, not executed by A-00b |
| Clean-environment installed package + SDK examples | venv outside the repo, no source `PYTHONPATH` | A-27b/A-28 (todos 49, 50) | `NOT_VERIFIED` |
| External plugin distribution install/enable/conflict | third-party wheel outside core | A-22b/A-23 (todos 41, 42) | `NOT_VERIFIED` |
| Selectivity + NMR/thermo cross-domain records | registered `experience:*` phase receipts with a real `run_id` | A-25b/A-26 (todos 46, 47) | `NOT_VERIFIED` |
| Live HTTP `GET /api/workflows` | the §8.1 count came from the in-process response builder, not an HTTP round trip against `acp run serve` | A-13-adjacent | in-process probe only |
| `reports/architecture_repair_20261022/REPORT.md` content | read **read-only from the primary checkout** (`...`); that directory does **not** exist in the A worktree (`git ls-files` shows it absent) | — | read from primary checkout; `static-fact` as a path fact, primary-checkout-only |

### 8.8 Reconciliation: 15 (code/plan) vs 14 (README / claimed API)

| Source | Claim | Verified state in this run |
|---|---|---|
| `README.md:118` | "`GET /api/workflows` 现恰好返回 14 个 active 工作流 … 勿硬编码旧的 15 项固定列表" | **Stale.** `GET /api/workflows` → `_build_workflow_info()` (`routes.py:53-61`) → `list_workflow_entries()` returns **15** records (executed in this run; HTTP round trip `needs-real-evidence`, §8.7) |
| `README.md:198,232,245,249,517` | "14 active" workflows / "14 个任务核心" (the `:232` list enumerates 14 and omits `xtb_constrained_opt`) / "14 个工作流…经 `cccp.calculation` 任务核心" | **Stale** — code has 15 active workflows and 15 task kinds (`XtbPathSearch`/`OrcaGradient`/`XtbConstrainedOpt` landed later; the 14-list predates `xtb_constrained_opt`) |
| `docs/reference/current/ACP_CCCP_Task_API_DevDoc.md:8,90,147,677,862` | "fourteen `run_*` task cores" / "all 14 workflows callable" | **Stale** by the same one item (`xtb_constrained_opt`) |
| Root `AGENTS.md` + `src/acp/workflows/AGENTS.md` | 15 workflows, `tasks/` 15 task cores | **Matches code** |
| `tests/test_architecture_invariants.py:91-112` | `len(active_ids) == 15` + exact id set | **Passes** (guard run in this task) |
| Plan A (todo 2 + `ACP_2.0_Parallel_Plan_A_CCCP_20261020.md`) | "fifteen active workflows and fifteen task kinds" | **Matches code** — the plan's 15 is authoritative for this track |

**Reconciliation ruling:** the executable surface is **15 workflows / 15 task kinds**; the "14"
figures are frozen prose from the pre-`xtb_constrained_opt` documentation state. `README.md`,
`ACP_CCCP_Task_API_DevDoc.md` and the nested `AGENTS.md` label drift are **B-owned files — A does not
edit them**; A records the drift here and publishes the corrected fifteen-task view in the A-06b
compatibility matrix (todo 14). The count discrepancy is inventory content, **not** a reason to
shrink the plan's 15 executable workflows.

### 8.9 Not-implemented / not-present list (as of this baseline)

| # | Item | Detail | Evidence |
|---|---|---|---|
| 1 | Capability matrix cells | 112/140 cells `NOT_IMPLEMENTED`, 2 `STUBBED` (crest `geometry_optimization`, `single_point`) | `static-fact` |
| 2 | Catalog non-active ids | 14 `retired` + 1 `planned` (`custom_sequence`); CLI interception via `_CLI_REMOVED_WORKFLOWS`; synthetic `fake` never public | `static-fact` |
| 3 | **GAP-8** package export gap | `run_xtb_constrained_opt` missing from `cccp.calculation` package-level lazy exports (runtime `AttributeError` this run); `TYPE_CHECKING` block also omits `run_scan` | `static-fact` (runtime-verified) → repaired by A-06b (todo 14) |
| 4 | `TaskKind` docstring drift | `requests.py:55` "seven core + seven P2" describes 14 while the enum holds 15 | `static-fact` (comment only; corrected by the A-06b matrix) |
| 5 | No capability registry / task binding / recipe compiler / deterministic validators | GAP-2/3 — A-05/A-06/A-07/A-11 deliver them; today capability resolution is only the inline selection seam (§8.4) | `static-fact` |
| 6 | Plugin manifest fields | GAP-7: no capability kind, implementation id, recipe/validator refs, trust level, distribution digest, verified evidence, declared-license policy; no `cccp.science_plugins` entry-point group; enablement in-memory; host not a sandbox | `static-fact` |
| 7 | `WorkflowSpec` unused at runtime | zero constructions outside `tests/test_core_workflow.py` (§8.1) | `static-fact` |
| 8 | CDXML input | `DEFERRED` outside initial A/B/C scope (R3-12); unsupported features must be rejected explicitly | `static-fact` (scope decision) |
| 9 | B01 receipt protocol | `acceptance-result.schema.json` / `acp2.acceptance.v1` and the `(owner, scenario, phase, run_id)` registry unpublished → A's `result.json` stays a provisional A-internal record | `needs-real-evidence` (B01) |
| 10 | Repair-report directory in A worktree | `reports/architecture_repair_20261022/` absent from the A worktree; `REPORT.md` read read-only from `repo-root` | `static-fact` (path fact) |
| 11 | Build dependency | `$ACP2_A_PYTHON` has `setuptools 65.5.1` but **no `wheel`**; editable install/wheel build needs a prepared build env (A-00a fallback) — no network install performed | `static-fact` (env), wheel build `needs-real-evidence` |
| 12 | Real QC/LSF/plugin/selectivity execution | see §8.7 | `needs-real-evidence` |

Machine-readable companions added by A-00b (same directory):
`cross-track-dependencies.json` (A's external edges, artifact-level readiness, AND/OR preconditions,
cycle-checked by `tests/test_acp2_science_inventory.py`) and `packaging-preview.md` (existing
distribution/ownership + minimal wheel smoke argv).
