# ACP2 Plan A — Science Baseline Record (A-00 / B00)

Recorded: 2026-10-22 (UTC run `a00-20261022T111022Z`)
Owner: A (science track). This file is the single baseline record consumed by tasks A-01…A-29.

## 1. Revisions

| Field | Value |
|---|---|
| Actual worktree HEAD at A-00 start | `096cdffe9a3817d8ee443d824830d87ec6e07bc2` (bare base, no business fixes) |
| Branch | `acp2/a-science` (worktree `/ACPServer/acp2-development/A`) |
| Frozen base revision (`ACP2_BASE_REV`) | `096cdffe9a3817d8ee443d824830d87ec6e07bc2` — overlay restore/apply base only; **not** the code candidate |
| B00 candidate id (`candidate-snapshot.json::candidate_id`) | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` |
| Code-candidate revision (declared in repair report, historical reference) | `ab492cc8be9e46125fa2d2fae69a3ecaac64a548` |
| **Delivery revision** (baseline commit created by A-00) | **`2e0ce9c202f5a65180aa97ca2787f82dc0054e6b`** |
| Commit subject | `chore(cccp): lock ACP2 science baseline and ownership boundary` |

`096cdff` is the Git starting point only. The executed code is the overlay-restored candidate; `2e0ce9c` is the commit that materializes it on `acp2/a-science`.

## 2. B00 overlay (authoritative source of truth)

| Field | Value |
|---|---|
| Overlay archive (concrete path) | `/ACPServer/AutoCalcPlatform/reports/architecture_repair_20261022/candidate-overlay.tar.gz` |
| Overlay archive SHA-256 | `3fb835b5069f96642bf6bb889a804532bbb70b69cf796d21208a6e3364117734` (matches `SHA256SUMS`) |
| `tracked-changes.patch` SHA-256 | `4578405dc1b99d9a884c651a8ea064a6eed311128b9564966200b33e95aa9242` |
| Snapshot-declared `tracked_patch_sha256` | `4578405dc1b99d9a884c651a8ea064a6eed311128b9564966200b33e95aa9242` (**match**) |
| `candidate-snapshot.json` SHA-256 | `33861de7d5578a8cfa5b48bcde27e7bd2b25ac3427c4dacb47c4eca921ae872c` |
| Patch content | 28 tracked files (`git apply` to `096cdff`) |
| New files | 144 files (`new-files/` copied over the worktree) |
| Excluded path | `tests/baseline/refactor-evidence/e2e-task-root.txt` — pre-existing local test-run marker; **not** committed, **not** modified |

Materialization commands (executed in `/ACPServer/acp2-development/A`):

```bash
tar -xzf /ACPServer/AutoCalcPlatform/reports/architecture_repair_20261022/candidate-overlay.tar.gz -C /tmp/opencode/a00-overlay-a00-20261022T111022Z
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
| Role interpreter (`$ACP2_A_PYTHON`) | `/ACPServer/acp2-development/envs/a-python/bin/python` (CPython 3.11.13) |
| Editable install | `"$ACP2_A_PYTHON" -m pip install -e "$ACP2_A_SOURCE" --no-deps --no-build-isolation` (exit 0) |
| `cccp` import | `/ACPServer/acp2-development/A/src/cccp/__init__.py` |
| `acp` import | `/ACPServer/acp2-development/A/src/acp/__init__.py` |
| `acp_services` import | `/ACPServer/acp2-development/A/src/acp_services/__init__.py` |
| All imports in source tree | **YES** (all three resolve under `/ACPServer/acp2-development/A/src/`) |

Build note: the venv has no `wheel` distribution, so the first editable build failed at
`error: invalid command 'bdist_wheel'`. The build was re-run with the already-present local
build environment on `PYTHONPATH`
(`/tmp/acp-repair-build-env/lib/python3.11/site-packages`, wheel 0.48.0); no network install and
no new runtime package was added.

## 5. Evidence root

`$ACP2_EVIDENCE` = `/ACPServer/acp2-evidence/a-science-20261022`

A-00 evidence: `/ACPServer/acp2-evidence/a-science-20261022/A/A-00/a00-20261022T111022Z/`
(`result.json`, `argv.json`, `stdout.log`, `stderr.log`, `pre-state.txt`,
`snapshot-verify.json`, `overlay-digests.txt`, `failure-qa.txt`).

## 6. Dirty-path inventory before materialization

### 6a. A worktree (`/ACPServer/acp2-development/A`)

Command: `cd "$ACP2_A_SOURCE" && git status --short && git diff --stat`
(at HEAD `096cdffe9a3817d8ee443d824830d87ec6e07bc2`, branch `acp2/a-science`):

```text
<no output>   # git status --short returned 0 lines
<no output>   # git diff --stat returned no changes
```

Inventory: **0 dirty paths** (clean worktree, no staged/unstaged/untracked entries). The A worktree
was created fresh from the bare base; no other agent had written to it.

### 6b. Primary checkout (`/ACPServer/AutoCalcPlatform`)

GAP-13 names the dirty primary checkout and its untracked planning documents as the baseline risk.
Observed by the orchestrator at **2026-10-22T11:06Z**, before A-00 materialized the overlay.
Branch `feat/frontend-plan1-closure`, HEAD `096cdffe9a3817d8ee443d824830d87ec6e07bc2`.
The primary checkout was **only read** by A-00 (overlay tarball, `REPORT.md`, `SHA256SUMS`, evidence
JSON and the cited plan documents); nothing was cleaned, reset, stashed, committed or otherwise written.

`git -C /ACPServer/AutoCalcPlatform status --short` — **39 modified + 11 untracked**:

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
- Repair report: `/ACPServer/AutoCalcPlatform/reports/architecture_repair_20261022/REPORT.md`
- Deliverable path map / ownership boundary: `path-map.md` (same directory)
