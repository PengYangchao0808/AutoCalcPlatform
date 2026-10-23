# B26a fix-4 re-run note (todo 37)

Candidate: `0.3.0` built from `abec2bc4f1d302827248d36171973df5bc1bc4dd`
(base B25b `a84c4266bfc4738c8efc2988b7c5c37079a8a324`) into
`../B25/release-fix4/`. Full evidence and digests: `../Bfix4/summary.json`.

The historical B26a matrix is updated in place; the two FAIL rows now re-run
against the fixed wheels and the remaining rows are unchanged.

| B26a row | Before | After fix-4 | Evidence |
|---|---|---|---|
| `authorization` (B15 revocation SSE) | FAIL | **PASS** | `../Bfix4/b15/` |
| `installed_workbench_submission` (B26A-D1) | FAIL | **PASS** | `../Bfix4/b26a-rerun/` |
| `third_party_plugin_install` | NOT_VERIFIED | NOT_VERIFIED (not re-run) | unchanged |
| `agent_research_entry` | NOT_VERIFIED | NOT_VERIFIED (no DSH/browser driver) | unchanged |
| `lsf` | NOT_VERIFIED | NOT_VERIFIED (no LSF client) | unchanged |
| `disconnect_recovery` | NOT_VERIFIED | NOT_VERIFIED (running-task recovery not re-run) | unchanged |

## B26A-D1 — installed Lab/task submission

- HTTP: `POST /api/v1/jobs` returned `201`; job `20261023_223703_002_b26a-fix4-http`
  reached `status=completed`. Real artifacts:
  `RESULT/task_result.json` (`status=completed`, `complete=true`),
  `RESULT/result_manifest.json` (`status=completed`, one product),
  `execution_receipt.json` (`state=completed`). Energy `-1.116759307204`
  Hartree (HF/STO-3G). Release `2c7ffd56045b5bd5`, digest
  `b59ec6b6b502ed70104baacb17b41873769813def34224840017792e438900b3`.
- CLI: `acp task execute --workflow singlepoint --input input.xyz --name
  b26a-fix4-sp2 --method HF --run-root <isolated> --follow` exited `0` with
  `status=completed`; real `RESULT/task_result.json` and manifest were
  produced; energy `-1.132971755441` Hartree (HF/def2-TZVPP; the CLI has no
  `--basis` option). The run root contains the immutable release built from
  installed packages: `releases/2c7ffd56045b5bd5/{acp,acp_services,cccp}`.
- The installed identity proof prints the three module `__file__` values under
  `site-packages` and shows the identity keys are package-relative
  (`acp/...`, `acp_services/...`, `cccp/...`), with no repo path.

## B26A-D2 — revocation SSE

The stream closed before revocation because the reused fixture job was
terminal: Controller restart froze the `PAUSED` fixture jobs to `FAILED`
(`[RESTART_FAILED]`), and the route emits `done` for terminal owners. This is a
harness fixture-state problem, not an installed-code delta. The harness now
resets reused fixture jobs to `PAUSED` with `JobStore.transition` CAS before
opening the stream. The strict assertion is unchanged. Re-run result:
`permission_changed_after_revocation=true` for both key and account
revocations, `stream_thread_stopped=true`, reads after revocation `401`.

## Package-install scheduler assertion

`package-install` now includes `b25.scheduler_task`: the installed Lab
completes a real H2/STO-3G singlepoint and the receipt release id equals the
installed-package identity. Result `PASS`; see
`../Bfix4/package-install/scheduler-task/scheduler-task.json`.
