# B14a re-freeze — base-suite failure classification

Command (exact; no ellipsis):

```bash
source /opt/acp2-dev/data/b/acceptance-env.sh
export ACP_RUN_ROOT=/opt/acp2-dev/data/integration/run-root
export ACP2_RUN_ROOT=/opt/acp2-dev/data/integration/run-root
export ACP2_PROJECT_ROOT=/opt/acp2-dev/integration
export PYTHONPATH=/opt/acp2-dev/integration/src
cd /opt/acp2-dev/integration
/opt/acp2-dev/venvs/b/bin/python -m pytest -m "not slow" -q
```

Candidate under test: `77ac1de1af6a5db6b57f4f904a859305ff73042c` (tree
`9809bea68e7132ae9ab5c0285b2c41373966f443`).

Result: **3 failed, 10649 passed, 119 skipped, 95 deselected, 29 warnings in
1795.32s (0:29:55)**. Exit code 1. This is **not** a clean PASS and is not recorded
as one.

The six B-owned failures that the previous candidate recorded are now **PASS** in
this suite (they do not appear in the FAILED list):

* `tests/test_acp_remote_catalog_prefetch.py::test_prefetch_skipped_for_non_terminal_job`
* `tests/test_acp_remote_catalog_prefetch.py::test_prefetch_skipped_for_local_job`
* `tests/test_acp_remote_catalog_prefetch.py::test_v2_reads_after_terminal_prefetch_fetch_on_demand_once`
* `tests/test_acp_lifespan_ownership.py::test_concurrent_lifespan_race_exactly_one_owner`
* `tests/test_f4_scope_audit.py::test_omo_no_new_files`
* `tests/test_f4_scope_audit.py::test_migration_scope_audit_reaches_migration_files`

## Remaining failures

| # | Test | Owner | Classification | Evidence |
|---|---|---|---|---|
| 1 | `tests/test_acp2_experience_harness.py::test_clean_live_environment_is_accepted` | C | C-owned harness test order-dependence, not product code and not merge-induced. `scripts/acceptance/acp2_experience_scenarios.py::assert_live_environment` scans `sys.modules` with a `fake/mock/stub × backend/transport/model/client` regex; a full-suite test module imports the legitimate third-party `httpx._transports.mock`, which matches the regex. | File alone: `38 passed in 0.83s` (single test `1 passed in 0.19s`). Reproduced: importing `httpx._transports.mock` via a pytest plugin before the single test gives `1 failed in 0.15s`. This is a new C cross-track request; B did not edit C harness code. |
| 2 | `tests/test_acp2_runtime_persistent_idempotency.py::test_crash_after_reservation_before_job_row` | B (test flake) | Timing-sensitive flake in the 0.05 s lease window; pre-existing, unrelated to the re-freeze. | Passed 3/3 reruns on the candidate: `1 passed in 1.70s`, `1.56s`, `1.54s`. |
| 3 | `tests/test_frontend_sync_viewer_framing.py::test_add_model_result_restricted_methods_pin` | C | C-owned frontend defect, still present at C `49ddfad`. `frontend/js/workbench/features/scientific-evidence.js` names a local callback variable `token` and calls `token.fn()`, which the addModel-token guard flags. B did not edit `frontend/**`. | Fails alone on the candidate: `1 failed in 0.99s` with `addModel() results are restricted tokens; native-only calls: workbench/features/scientific-evidence.js: token.fn()`. |

## Net

* No failure is attributable to the A/B/C domain-ownership merge resolution.
* No new B-owned product or guard failure exists.
* The two C-owned rows keep H3 open (C must fix the harness module scan and the
  `scientific-evidence.js` token collision); the timing flake is not a candidate
  defect and passed on rerun.
* The candidate remains frozen with `base_suite.result = "FAIL"`; the plan forbids
  turning a known FAIL into a PASS.
