# B41b defect remediation — fix verification (todo 79)

**Date:** 2026-10-19
**Scope:** B-lane files only: `src/acp/scheduler/runner.py`,
`src/acp/scheduler/manager.py`, `src/acp/cli.py` (task recover + doctor),
`tests/test_services_b41b_defect_fixes.py`.
**Source defects:** `docs/reference/next-change/ACP_Services_Standalone_Recipe.md` §2.1
(measured in B41b; repo `48b1660`).

## Root causes and fixes

| Defect | Root cause (measured) | Fix |
|---|---|---|
| D1 cross-process finalization | `JobRunner.poll()` untracked-RUNNING branch returned `record.exit_code or 1`; a worker that exited after its launching controller had exited never wrote `record.exit_code`, so the next controller finalized a completed workflow as FAILED. Cross-process CANCELLING lacked the in-process cancel event and was finalized FAILED. | `JobRunner.disk_completion_exit_code()` reads `.exit_code`/`state.json` (attempt-fenced) before finalizing; CANCELLING rows return the cancel code. `JobManager._persist_terminal()` treats a persisted local CANCELLING row whose process is gone as the confirmed stop → CANCELLED (control intent not overwritten). |
| D2 recover config loss | `_build_task_plan()` dropped `CalculationRequest.method` and never loaded `executables.*`; the executor fell back to `plan.profile` (`default`) as the method and ORCA resolved through PATH (`/usr/bin/orca`, GNOME reader). | `_build_task_plan()` now emits a step spec carrying the real method + the merged config (user → project → explicit `--config` → env → CLI); `recover` reads the persisted `job.json`/`task.json` workflow/method/resources and resolves CLI > persisted > default. |
| D3 startup-window SIGKILL orphan | A controller killed between spawning the worker and persisting RUNNING left a live worker; the next controller either refused to start a second execution (and marked FAILED) or ignored the orphan. | `JobManager` adopts a live worker whose row never reached RUNNING (attempt-tagged `run.lock` + `/proc` identity), and `cancel()` confirm-stops an untracked worker. Any failed submission confirm-stops local orphans before FAILED is persisted. |
| S4c invalid configured binary reported PASS | `resolve_executable()` silently falls back to PATH when the configured path is invalid, so doctor reported the GNOME Orca reader as PASS. | `acp doctor` uses `resolve_executable_with_source()`: a configured pin whose winning source is not `config` is FAIL (never a PATH PASS). PATH results pass a QC sanity check (rejects the GNOME Orca Python reader / other impostor scripts); unconfigured PATH hits are NOT_VERIFIED, not PASS. |
| S4 local-only doctor exit 1 | `acp doctor` only checked remote nodes and exited 1 for zero nodes. | With `deployment.usage=local` (or tier `local-single`/`standalone`) and zero nodes, doctor prints a local PASS/FAIL/NOT_VERIFIED capability report (software, run_root, store, config) and exits 0 only when no FAIL. Remote-node doctor behavior is unchanged. |

## Actuals — isolated `/tmp` run_root, real CLI

Environment: `ACP_RUN_ROOT=/tmp/todo79_runs`, cwd `/tmp/todo79_work`
(no project config), isolated explicit config `/tmp/todo79_home/.cccp.yaml`
(orca `/home/<user>/orca611/orca`, xtb `6.7.1`), OpenMPI bin on PATH.

### D2 — real ORCA recover (before: `! default def2-TZVPP` + GNOME fallback)

```
$ acp task recover --task-dir /tmp/todo79_tasks/recover \
      --workflow optimize --method r2SCAN-3c \
      --config /tmp/todo79_home/.cccp.yaml
{"command": "recover", "completed": true, "method": "r2SCAN-3c",
 "status": "completed", "workflow": "optimize"}
exit 0
$ head -1 WORK/03_OPT/optimize.inp
! r2SCAN-3c Opt
$ grep 'Program Version' WORK/03_OPT/optimize.out
Program Version 6.1.1 - RELEASE
```

`! default` / `def2-TZVPP` placeholder absent; the executed binary is the
configured ORCA 6.1.1 (the GNOME `/usr/bin/orca` cannot produce an ORCA log).

### D1 — cross-process rerun with no live controller

```
$ acp task rerun --job 20261019_183706_001_xyz1        # new process; exits
{"attempt": 2, "command": "rerun", "status": "queued"}
... worker completes; state.json: "status": "completed"
$ acp task query --job ...                             # DB row still RUNNING
$ <next controller constructs JobManager(reconnect_local_workers=True)>
POLLED completed 0 1.0
$ acp task query --job ...
{'status': 'completed', 'exit_code': 0, 'attempt': 2}
```

Before the fix the same sequence produced `failed` / `exit_code=1`.

### D1 — cross-process cancel of an already-finished worker

```
$ acp task rerun --job ...          # attempt 3, state.json completed
$ acp task cancel --job ...
{"attempt": 3, "command": "cancel", "status": "cancelling"}
$ <next controller poll>
POLLED cancelled 130 3
```

Before the fix the CANCELLING row was finalized `failed`.

### D3 — startup-window orphan

A seeded STARTING row plus a live worker bound to its task directory:
`JobManager(reconnect_local_workers=True)` logs
`Adopted startup-window worker for job d3-orphan (pid=…)`; a cross-process
`cancel` confirm-stops it and the next poll reports `cancelled 130`; no
process remains bound to the task directory (`/proc` scan empty). The worker
was never double-dispatched.

### S4c — explicit invalid ORCA config

```
$ cd /tmp/todo79_doc   # local cccp.yaml: deployment local + orca /nonexistent/orca
$ acp doctor
Local capability report (usage=local, tier=local-single)
  FAIL（1）：
    - software:orca — 已配置路径不可用：/nonexistent/orca（拒绝 PATH 回落：/usr/bin/orca）
Local doctor finished: 1 failure(s) found
exit 1
```

No `software:orca — /usr/bin/orca (source=…)` PASS line exists.

### S4 — local-only doctor

```
$ acp doctor          # deployment local/standalone, zero remote nodes
No remote nodes configured — running local capability checks (local/standalone deployment).
Local capability report (usage=local, tier=local-single)
  PASS（10）：software:{orca,xtb,crest,censo,shermo,isostat,molclus}, run_root, store, config
  FAIL（0）：- （无）
Local doctor finished: all checks passed
exit 0
```

Remote-node doctor output is untouched (regression test pins the `== node … ==`
/ `Doctor finished: all checks passed` surface).

## Automated verification

`tests/test_services_b41b_defect_fixes.py` — 15 passed (D1 evidence and
manager finalization, cross-process rerun/cancel, D3 adoption/queued
adoption/orphan cancel, D2 plan+recover with a fake capability backend,
S4c negative, PATH sanity, S4 local-only, remote-doctor regression).

Required regression suite:

```
python3.11 -m pytest tests/test_execution_integrity_e2e.py \
  tests/test_remote_cancel_confirmation.py tests/test_remote_submission_protocol.py \
  tests/test_acp_scheduler.py tests/test_services_cli_full_loop.py \
  tests/test_services_cli_offline_loop.py tests/test_acp_services_isolation.py \
  tests/test_architecture_invariants.py -q
```

## Residual / handoff

`src/cccp/software.py::resolve_executable` still silently falls back to PATH
when a configured path is invalid. `acp doctor` is truthful through
`resolve_executable_with_source`, but the `acp init` embedded capability
report still calls the raw resolver. This C-side root cause is registered as a
pending C handoff in `tests/baseline/services-evidence/handoff_registry.md`.
No `src/cccp/**` file was edited by this task.

No service restart was performed; all work used isolated `/tmp` run roots.
