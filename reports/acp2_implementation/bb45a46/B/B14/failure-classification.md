# B14a base-suite failure classification

Command (exact; no ellipsis):

```bash
source /opt/acp2-dev/data/b/acceptance-env.sh
export ACP_RUN_ROOT=/opt/acp2-dev/data/integration/run-root
export ACP2_RUN_ROOT=/opt/acp2-dev/data/integration/run-root
export PYTHONPATH=/opt/acp2-dev/integration/src
cd /opt/acp2-dev/integration
/opt/acp2-dev/venvs/b/bin/python -m pytest -m "not slow" -q
```

Candidate under test: `fbac5abb50ba7f86603b786fc98d857df52f5f2a`
Result: **9 failed, 10539 passed, 119 skipped, 95 deselected, 29 warnings in 1782.73s**.
This is **not** a clean PASS and is not recorded as one.

The unmerged B candidate `2a57d6b6d91427093c6a10e710e5a52446ed801e` was run with the
same interpreter and an isolated run root for the six B-owned failing tests; the raw
log is `base-b-candidate-failures.log`.

| # | Test | Classification | Evidence |
|---|---|---|---|
| 1 | `tests/test_acp2_experience_harness.py::test_clean_live_environment_is_accepted` | Test-harness/environment, not product code. C's `runtime_env` fixture falls back to `scripts/acceptance/acp2_runtime.py` when `ACP2_PROJECT_ROOT` is unset (`tests/test_acp2_experience_harness.py:69-70`). | Rerun on the candidate with `ACP2_PROJECT_ROOT=/opt/acp2-dev/integration`: `1 passed in 0.12s`. |
| 2 | `tests/test_acp2_runtime_persistent_idempotency.py::test_crash_after_reservation_before_job_row` | Timing-sensitive flake in a 0.05 s lease window, not a code delta. | Passes on `2a57d6b` (base run) and on the candidate rerun (`1 passed in 1.58s`). |
| 3 | `tests/test_acp_remote_catalog_prefetch.py::test_prefetch_skipped_for_non_terminal_job` | Pre-existing B defect: `JobManager._prefetch_remote_catalog` calls `self._prepare_executor.prefetch()` (manager.py:5484), but `_CompositePrepareExecutor` exposes only `execute` (services_data_bootstrap.py:1120). Identical at `2a57d6b`. | Fails on `2a57d6b` in `base-b-candidate-failures.log`. |
| 4 | `tests/test_acp_remote_catalog_prefetch.py::test_prefetch_skipped_for_local_job` | Same pre-existing B defect as #3. | Fails on `2a57d6b`. |
| 5 | `tests/test_acp_remote_catalog_prefetch.py::test_v2_reads_after_terminal_prefetch_fetch_on_demand_once` | Same pre-existing B defect as #3. | Fails on `2a57d6b`. |
| 6 | `tests/test_acp_lifespan_ownership.py::test_concurrent_lifespan_race_exactly_one_owner` | Pre-existing B race/flake; the two lifespans serialized and produced `['ok']` with no refusal, and the captured thread warning shows `sqlite3.OperationalError: database is locked`. | Fails on `2a57d6b` in `base-b-candidate-failures.log`. |
| 7 | `tests/test_f4_scope_audit.py::test_omo_no_new_files` | Pre-existing B rule violation: `2a57d6b` already tracks five `.omo/evidence/task-*.md` files, so the guard fails before this integration. The candidate tracks 27 `.omo/` paths after the B00 overlay restoration (22 planning files added); the status is still the same pre-existing FAIL. | Fails on `2a57d6b` in `base-b-candidate-failures.log`. |
| 8 | `tests/test_f4_scope_audit.py::test_migration_scope_audit_reaches_migration_files` | Pre-existing B audit gap: `src/acp/services_mcp_bootstrap.py` is changed outside the registered migration stations. The file is byte-identical between `2a57d6b` and the candidate. | Fails on `2a57d6b` in `base-b-candidate-failures.log`. |
| 9 | `tests/test_frontend_sync_viewer_framing.py::test_add_model_result_restricted_methods_pin` | Pre-existing C defect, inherited by inclusion. `frontend/js/workbench/features/scientific-evidence.js:751` names a local callback variable `token` and calls `token.fn()`; the B guard's addModel-token regex flags the name collision. The file is C-owned and B did not edit it. | Fails on C HEAD `e03a9dd9f6897ca0a8d43e69a10b22a20097f05d` with the same message; passes on unmerged B `2a57d6b`. |

## Net

* 6 failures are pre-existing on the unmerged B candidate.
* 1 failure is pre-existing on the unmerged C track.
* 1 failure is a timing flake that passes on rerun.
* 1 failure is a test-harness path fallback that passes with the correct project root.

No failure is attributable to the A/B/C domain-ownership merge resolution. The
candidate is still frozen with `base_suite.result = "FAIL"` because the plan forbids
turning a known FAIL into a PASS; B14b/H3 must treat H3 as not closed for the failing
rows until the owning tracks fix them. C's scientific-evidence guard row is recorded
`NOT_VERIFIED` in `candidate.json`.
