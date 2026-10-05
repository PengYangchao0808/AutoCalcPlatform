# scheduler/remote/ — Remote LSF Execution

## OVERVIEW
Dispatching ACP jobs to remote compute nodes over SSH/SFTP + LSF (bsub/bjobs/bkill). Self-contained subpackage under the scheduler — the **only** scheduler component that talks to external machines. 11 modules, ~3900 lines; `runner.py` (1213 L) is the largest module in the entire project. Requires the `remote` extra (paramiko). Root parent: `../AGENTS.md` (scheduler).

## STRUCTURE
```
remote/
├── __init__.py          # 30+ re-exported symbols (config, ssh, sftp, sync, script_gen, monitor, runner, fetcher, cleanup, node_manager)
├── config.py            # RemoteExecutionConfig, RemoteNode dataclasses (9824 B)
├── ssh.py               # SSHConnectionPool — thread-safe, per-node paramiko pooling (323 L)
├── sftp.py              # FileStager — SFTP upload/download/incremental log tail (11575 B)
├── sync.py              # CodeSyncer — incremental mtime-based source sync to nodes (261 L)
├── script_gen.py        # build_lsf_script_spec / generate_lsf_script / build_remote_cli_command / derive_lsf_resources (360 L)
├── paths.py              # Remote storage identity (D01/contract B): derive remote dir from allocated storage — <remote_work_dir>/<project_leaf>/<task_leaf[__NN]>; single persisted path key result["remote_dir"]; job.json/task.json ownership probe; legacy flat dirs adopted read-only (200+ L)
├── release.py            # Content-hash code releases (D03): verify-then-publish to <remote_code_dir>/releases/<release_id>/ (immutable after .complete), ref/GC coordination locks, prune_releases (900+ L)
├── submission.py          # Submit identity + submit-right lease (D02/contract A): submission_id = sub_+sha256(job_id:attempt)[:16], owner token, wall-clock-UTC lease — pure stdlib, no paramiko
├── monitor.py           # RemoteJobMonitor — bjobs polling + log tail + bkill cancellation (341 L)
├── runner.py            # RemoteJobRunner — end-to-end orchestrator: submit/monitor/cancel/cleanup (1213 L)
├── fetcher.py           # RemoteResultFetcher — on-demand SFTP file/log retrieval (464 L)
├── cleanup.py           # RemoteCleanup — retention-based job-dir cleanup + disk-pressure housekeeping (514 L)
└── node_manager.py      # NodeManager/NodeStatus — node state with 30s TTL cache (315 L)
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Config models | `config.py` | `RemoteExecutionConfig` (hosts, paths, LSF params) + `RemoteNode` |
| SSH connection pool | `ssh.py` | Thread-safe; raises `SSHExecutionError`; **never leak connections — use context manager** |
| File transfer | `sftp.py` | `FileStager` — upload/download + `tail_log` incremental fetch |
| Code sync | `sync.py` | mtime-based incremental sync; `SyncResult` reports transferred/skipped |
| LSF script | `script_gen.py` | `derive_lsf_resources` (nproc/mem/queue) → `generate_lsf_script`; shared flag resolution via `jobs.xtbmd_method_flags` (E7 parity — add new xtbmd flags in `scheduler/jobs.py`, NOT here) |
| Job lifecycle | `runner.py` | `RemoteJobRunner.run()` — bsub submit → poll state.json → fetch results → cleanup; `state.json` observation on node |
| Monitor | `monitor.py` | bjobs parse, per-job log tailing, bkill on cancel |
| Result fetch | `fetcher.py` | On-demand file retrieval; `NotARemoteJobError` for local jobs; `write_file` uploads bytes back into the remote job dir (PES review write-back; wraps failures in `RemoteWriteError`) |
| Disk cleanup | `cleanup.py` | Retention-based sweeps + pre-submit disk-pressure check (`DISK_CLEANUP_THRESHOLD`/`DISK_SKIP_THRESHOLD`) |
| Node state | `node_manager.py` | 30s TTL cache of node status — call through NodeManager, not direct SSH |
| Remote dir mapping (D01) | `paths.py` | `resolve_remote_dir()`: explicit → `result["remote_dir"]` → `remote.relative` → dual-candidate ownership probe (new-style `<remote_work_dir>/<项目叶子>/<任务叶子>` vs legacy flat) → fallback flat. Ownership evidence = `job.json`/`task.json` markers; foreign owner → `RemoteDirConflictError` (never delete). Storage identity stable across attempts (rerun/continue/edit-recalculate reuse one dir). |
| Code releases (D03) | `release.py` | `ensure_node_release()` (ref → verify/upload staging → publish under lock, `.complete` last) / `ReleaseBinding` / `prune_releases()` (refs refresh + eligibility + delete/isolate all inside the node-level coordination lock; called only from `cleanup_old_jobs` with `with_release_gc=True`). Releases live in the INSTALL dir (`<remote_code_dir>/releases/`), never inside task dirs; binding persisted in the job record BEFORE bsub. |
| Submission protocol (D02) | `submission.py` | `submission_id_for()` (`sub_`+sha256(job_id:attempt)[:16], persisted before bsub, `-J acp_<submission_id>`), `lease_ttl_seconds()` (derived from real connect/read/bsub timeouts), `submit_lease_valid()` — while a lease is valid, reconcile must NEVER judge `not_accepted`. |

## CONVENTIONS
- **Paramiko only**: no subprocess ssh/rsync/scp — everything via paramiko SSHClient/SFTPClient
- **Thread safety**: `SSHConnectionPool` is the single shared resource; FileStager/Monitor operate per-job
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations`
- **`__all__`**: every module exports `__all__`; `__init__.py` aggregates 30+ symbols
- **`type: ignore` heavy in runner.py** (13 sites — state-dict unpacking from remote JSON)
- **CLI command generation**: `build_remote_cli_command` produces `python -m acp.cli run <workflow> ...` argv — remote nodes run the synced codebase, not an installed package

## STATE-TRANSITION RULES（execution-integrity，2026-10-06）
> Root `AGENTS.md` § EXECUTION PROTOCOL；spec：`docs/ACP_Job_File_Layout_Spec.md` §2b + `docs/ACP_Execution_Integrity_Rebaseline_Notes.md`。

- **CAS transitions**: 本地 job 状态只经 `scheduler/store.py` CAS API 变更（`revision`/`status`/`attempt` 守卫）；远端 `state.json`/bjobs 只是**观测**，poller 只做前进式提案、绝不覆盖用户控制意图（pause/cancel），终态不可复活。
- **submit states（契约 A）**: `submit_state="intent"`（含 `submission_id`、owner token、`lease_expires_at` wall-clock UTC）先于 bsub 持久化 → `submitted`；`on_submitted` 重读 revision 有界重试；租约有效期间 reconcile 永不判 `not_accepted`；`not_accepted` 只由 5 条证据成立**且提交权租约已失效**。
- **cancel states**: CANCELLING 只有**确认远端已停止**（bkill + bjobs 核对消失）后才置 CANCELLED；取消与 bsub 协调至恰好一种结局、无重复 bsub；live owner 永不产出 `not_accepted`。
- **poll/reconcile ownership**: 观测写归 runner/monitor，控制意图归 manager；`bjobs` `STATUS_UNKNOWN`/多匹配保持未决，不下结论。

## ANTI-PATTERNS
- **`runner.py` monolith**: 1213 lines, single orchestrator — hardest file in the project to modify; test via `test_remote_phase*.py` mocks
- **Bare `except Exception:`**: ~10 sites swallow errors — `runner.py:201/292/452/627/752/821/830/1076/1191/1305` (mostly debug-logged, 1076 silent), `monitor.py:141/186/205/231`, `ssh.py:115` (return False), `cleanup.py:449/463`, `sftp.py:256`
- **Generated-script strings contain fake `except Exception:`** at `runner.py:1025/1042` — these are literal text inside the LSF submission script template, NOT real handlers; do not "fix" them
- **`type: ignore` density**: 13 in runner.py alone — typed-state contracts with remote JSON are unresolved
- **No local fallback**: remote failures surface as `RemoteNodeUnavailableError`/`RemoteSubmissionError` — job manager must handle, not the remote layer

## NOTES
- Tested by `tests/test_remote_phase{1..6}.py` — mock paramiko (FakeSFTPFile/FakeSFTPClient); phase1_integration is `@pytest.mark.integration`
- Requires `pip install -e '.[remote]'` (paramiko)
- CLI-flag parity (E7): shared resolution lives in `scheduler/jobs.py` (`censo_preset_from_method`, `xtbmd_method_flags`, `_as_bool`) — used by BOTH local `runner.py` and remote `script_gen.py`
