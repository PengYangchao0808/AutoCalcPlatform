# scheduler/ — Phase 2 Task Scheduler

## OVERVIEW
Job queue, persistence, and background execution for ACP calculations. Receives JobSpec from CLI or API, persists to SQLite, runs as subprocess via `python -m acp.cli run <workflow>`, captures artifacts/provenance. **45 .py（31 top-level + remote/ 14；`manager.py` 7051 L + `runner.py` 2307 L 为主执行面）**. Store/schema implementations moved in place to `src/acp_services/store/` (B05-7 / todo 77); `store.py`（18 L）and `migrations.py`（16 L）here are **identity forwarders**, not implementations. `projects.py` / `molecule_groups.py` / `naming.py` are same-object forwarders to `acp_services` too. Research/approval/recovery implementations live in `acp_services.runtime` — `{approvals,research_budget,research_events,recovery,attempt_recovery,attempt_freeze,outbox,events,adapter_registry,analysis_capabilities,capability_registry,local_admission,step_dag}.py`; this package consumes them only through manager seams (`attempt_completion` / `attempt_publication`), `acp.services_*_bootstrap` and API routers.

## STRUCTURE
```
scheduler/
├── __init__.py           # Selective public re-exports (155 lines)
├── jobs.py               # JobStatus, JobSpec, JobRecord + CLI-flag helpers (981 lines)
├── manager.py            # JobManager — submit/cancel/pause/unpause/continue/rerun/purge + prepare executor/worker + attempt completion/publication seams + structure_cache + run-root claims (7051 lines)
├── runner.py             # JobRunner — subprocess execution, stdin materialization, artifact capture (2307 lines)
├── store.py              # Compat forwarder → acp_services.store.store (18 lines); identity, never a second implementation
├── migrations.py         # Compat forwarder → acp_services.store.migrations (16 lines)
├── registration.py       # register_completed_cli_job — CLI --register → COMPLETED job (store-layer only; never constructs JobManager) (215)
├── stage_tasks.py        # StageTask/StagePlan + PlanCompiler + StageTaskStore + StageTaskObserver (731)
├── tasks.py              # TaskIndex — v2 task metadata SQLite index + custom_name/name_revision (883)
├── task_views.py         # View engine: group/filter/sort/count/facets (871)
├── job_edit.py           # Edit draft + preview fingerprint + EDIT_ACTIVE_WORKFLOWS + JobEditOperationStore (977)
├── molecule_groups.py    # Compat forwarder → acp_services.store.molecule_groups (16)
├── naming.py             # Compat forwarder → acp_services.store.naming (15)
├── projects.py           # Compat forwarder → acp_services.workspace.projects (18)
├── structure_source_store.py    # Structure-source org/query schema + metadata + RevisionConflictError (2402)
├── structure_source_indexer.py  # Paged backfill + 60s incremental sweep daemon (443)
├── structure_sources.py         # Discover reusable structures from terminal jobs (1456)
├── provenance.py         # Provenance, ParserRegistry, compute_input_hash, audit events (203)
├── events.py             # JobEventLog — per-job events.jsonl append/read + after_seq cursor (215)
├── artifacts.py          # Artifact + ArtifactRegistry + capture_stage_artifacts (266)
├── files.py              # build_manifest + resolve_safe path-traversal guard (169)
├── logs.py               # read_log_tail / read_log_range (149)
├── local_cleanup.py      # Local run_root retention sweeps (909)
├── nodes.py              # Execution-target model + NodeRegistry + target validation (621)
├── capabilities.py       # derive_required_software + node capability matching (527)
├── metrics.py            # Display-only QC runtime metric extraction from stdout (120)
├── auto_tags.py          # User auto-tag rules applied at submit/backfill (91)
├── edit_parameters.py    # Project edited level fields into execution aliases (399)
├── input_snapshot.py     # Durable submitted-XYZ snapshot helper (37)
├── resources.py          # Resolve scheduler resource intent once (49)
├── processctl.py         # Cross-restart discovery/termination of task-owned processes (281)
└── remote/               # 14 .py — LSF bsub/bjobs, SSH/SFTP, code sync, fetch, cleanup (see remote/AGENTS.md)
```

## WHERE TO LOOK
| File | Key contents |
|------|-------------|
| `jobs.py` | JobStatus (pending/running/completed/failed/cancelled), JobSpec (workflow/input/params), JobRecord (status+timestamps+metadata) |
| `jobs.py` helpers | `censo_preset_from_method` / `censo_solvent_from_method` / `censo_ewin_from_method` / `xtbmd_method_flags` (+ `_as_bool`) — shared CLI-flag resolution used by both `runner.py` and `remote/script_gen.py` (E7 parity; add new xtbmd flags here, not in either file; boolean method values are string-tolerant) |
| `manager.py` | JobManager — entry point. `submit(spec, ..., budget=)` validates → persists via JobStore → dispatches; `cancel()` sets event; `_run_job()` invokes JobRunner. Startup order: `_claim_run_root()` (process-wide `_RUN_ROOT_CLAIMS`) → `_adopt_live_orphans_on_startup()` when `reconnect_local_workers=True`, else `_requeue_active_on_startup()` + `_dispatch_queued_jobs()` → `_queue_startup_catalog_prefetch()` → `_resume_pending_publications()` → `_sweep_outbox()`. Owns `structure_cache` (`RemoteStructureCache` singleton) and `_prepare_operations` (`PrepareOperationService`) / `_prepare_executor` / `_prepare_worker` (persistent prepare ops; `_queue_catalog_prefetch` enqueues terminal remote catalog fetch as a durable prepare op). Injects `attempt_completion` / `attempt_publication` seams; `_resume_pending_publications()` retries publication only. |
| `runner.py` | JobRunner — subprocess execution. `run()` builds CLI cmd, spawns subprocess, monitors via `_monitor()`, observes stage state via `StageTaskObserver`, captures artifacts via `capture_stage_artifacts()`, stores provenance. `materialize_job_input()` writes SMILES/XYZ to disk. `_run_fake()` for testing. |
| `store.py` | Compat forwarder → `acp_services.store.store`; `JobStore is acp_services.store.store.JobStore`. SQLite persistence for JobRecord, schema init, CRUD, project filtering, count aggregation. |
| `store.py` CAS APIs (execution-integrity) | Implementation lives in `acp_services.store.store`; forwarded names: `transition()` — CAS single-row state UPDATE guarded by `revision` + `status` + `attempt` (stale write → `JobStateConflictError`, never a blind overwrite); `update_progress()` (progress writes cannot clobber status/spec/result); `update_execution_identity()`; `requeue_with_spec()` — one-transaction in-place rerun (new spec + `attempt=attempt+1` + QUEUED). `attempt` is 1-based and **the scheduler is its single source** — remote/state.json observations never bump it. |
| `registration.py` | `register_completed_cli_job()` — X1′-D CLI visibility: persists a finished CLI run dir (e.g. `acp run XtbPathSearch --register`) as a COMPLETED JobRecord via JobStore.create + TaskIndex.sync_from_job + ProjectManager.ensure_default_project. Binds `work_dir` to the existing `--output` dir (ANTI-PATTERN #15: job id never enters the path). Skips scheduler task dirs (already registered by submit). CLI-only path — never constructs JobManager (single-instance lock). |
| `tasks.py` + `task_views.py` | `TaskIndex` — server-side SQLite index of v2 task metadata; custom_name/name_revision with transactional revision check (`NameRevisionConflictError`), NOT in `_SYNC_COLUMNS`. `task_views.py` — pure-read group/filter/sort/count/facets engine (status counts unaffected by limits). |
| `job_edit.py` | `EDIT_ACTIVE_WORKFLOWS` coverage registry + `audit_workflow_edit_coverage()`; draft restoration, editable-spec diffing, preview fingerprints, idempotent `JobEditOperationStore`. |
| `stage_tasks.py` | StageTask/StagePlan dataclasses, StagePlanProvider protocol, PlanCompiler (generic stage-plan compilation from METHOD_SCHEMAS), StageTaskStore (SQLite CRUD), StageTaskObserver (polls `<work_dir>/stage_tasks/*.json`, mirrors to DB). |
| `events.py` | JobEventLog — append-only JSONL per job (`WORK/00_RUNTIME/events.jsonl` on v2 task dirs, task root on legacy). SSE replays history then tails the file; `after_seq` / `Last-Event-ID` give a line-index replay cursor. |
| `artifacts.py` | Artifact (type/path/checksum/context), ArtifactRegistry (SQLite CRUD by job/task/type), `capture_stage_artifacts()` scans the supplied stage dir (runner passes `RESULT/`), computes SHA-256 checksums. |
| `provenance.py` | Provenance dataclass (input_hash, command_line, wall_time, parser_results), ParserRegistry (type→callable), `compute_input_hash()`, `build_provenance_for_job()`. |
| `files.py` + `logs.py` | `files.py`: result-file manifest + `resolve_safe()` traversal guard. `logs.py`: bounded `read_log_tail` / `read_log_range`. |
| `projects.py` / `molecule_groups.py` / `naming.py` | Identity forwarders → `acp_services.workspace.projects` / `acp_services.store.molecule_groups` / `acp_services.store.naming`; same objects, no local logic. |
| `nodes.py` + `capabilities.py` | Execution-target model (`local` first-class; remote nodes) + `NodeRegistry` / `validate_submission_target`; `derive_required_software` + `matches_capabilities` / `NoCapableNodeError`. |
| `structure_source_store.py` / `structure_source_indexer.py` / `structure_sources.py` | Org/query store (self-managed schema, `source_uid_for()` = `ss_`+sha256, optimistic `RevisionConflictError`) + paged backfill/60s sweep daemon + terminal-job structure discovery. |
| `local_cleanup.py` + `processctl.py` | Local retention-driven run_root cleanup; cross-restart discovery/termination of task-owned process groups. |
| `metrics.py` / `auto_tags.py` / `edit_parameters.py` / `input_snapshot.py` / `resources.py` | Display-only metric sidecar; auto-tag rules; edited-level → execution-alias projection; durable input XYZ snapshot; one-shot resource-intent resolution. |
| `migrations.py` | Compat forwarder → `acp_services.store.migrations`; `migrate is acp_services.store.migrations.migrate`. Runs schema version check + pending migrations; auto-called by JobStore/ProjectManager init. |
| Research/recovery integration | `acp_services/runtime/`（实现根，不属本包）: `approvals`, `research_budget`, `research_events`, `recovery`, `attempt_recovery`, `attempt_freeze`, `outbox`, `events`, `adapter_registry`, `analysis_capabilities`, `capability_registry`, `local_admission`, `step_dag`. scheduler 仅经 manager seams + `acp.services_*_bootstrap` + API routers 消费。 |
| Event-cursor recovery contract | `acp2.runtime.events-recovery.v1` = `acp_services.runtime.{events,outbox,recovery}`（per-owner durable `seq` cursor, `after_seq` replay）+ `scheduler/events.py` JSONL + `GET /api/v1/jobs/{id}/events?after_seq=`；证据 `reports/acp2_implementation/bb45a46/B/B19/`。 |
| `remote/` | Remote LSF execution — SSH/SFTP pool, code sync, bsub/bjobs, fetch, cleanup (see `remote/AGENTS.md`). |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` (matches `acp/` style)
- **Docstrings**: Compact, one-line where possible. Module-level docstrings in all files
- **`_utc_now_iso()`**: single implementation `acp_services.runtime.helpers.utc_now_iso`, imported as `_utc_now_iso` where needed (no local copies)
- **SQLite**: JobStore lives in acp_services; StageTaskStore, ArtifactRegistry, StructureSourceStore, TaskIndex, JobEditOperationStore each open their own SQLite connections. Schema via `sqlite3` directly, no ORM
- **`__all__`**: Every module exports `__all__`. `__init__.py` aggregates selectively
- **Threading**: Job lifecycle via `threading.Thread` + `threading.Event` (cancel signal). No asyncio in scheduler
- **Event files**: Per-job JSONL event log via `runtime_file()` — `WORK/00_RUNTIME/events.jsonl` on v2 layout, task root on legacy. Read via JobEventLog, not the SQLite DB
- **`stage_tasks/` convention**: Stage task state → JSON files under `<work_dir>/stage_tasks/`. StageTaskObserver polls these files
- **CLI-flag parity (E7)**: shared resolution in `jobs.py` (`censo_preset_from_method` / `censo_solvent_from_method` / `censo_ewin_from_method` / `xtbmd_method_flags` + `_as_bool`) used by BOTH `runner.py` and `remote/script_gen.py` — add new workflow flags here, not in either file

## STATE TRANSITIONS（execution-integrity 四不变量，2026-10-06）
> 详见 root `AGENTS.md` § EXECUTION PROTOCOL 与 `docs/reference/current/ACP_Execution_Integrity_Rebaseline_Notes.md`。

- **CAS-only state writes**: 一切 job 状态写入走 `store.py` 的 CAS API（`transition`/`update_progress`/`update_execution_identity`/`requeue_with_spec`，`revision`/`status`/`attempt` 守卫，冲突 → `JobStateConflictError`）。用户 pause/cancel 意图永不被过期 poll 观测覆盖；`PENDING/PAUSED→RUNNING` 合法转换不丢失；终态不可被旧写入复活；进度/工件写不得覆盖 status/spec/result；终态副作用在终态持久化**之后**且幂等可重试。
- **submit/cancel states**: `result["remote"]["submit_state"]`（`intent`（先于 bsub 持久化，含 `submission_id`/owner token/lease）/`submitted`/…）与 `cancel_state`（CANCELLING 只有**确认远端已停止**后才置 CANCELLED）是 job-detail/v1 recovery 矩阵的投影字段（`v1_schemas.py::JobRecovery.submit_state/cancel_state`）。
- **poll/reconcile ownership**: poll 观测只允许"低态→高态"前进式更新且必须持锁重读 revision；提交权租约有效期间 reconcile 永不判 `not_accepted`（`remote/submission.py`）；manager 拥有控制意图（pause/cancel/rerun），runner/observer 只提案观测值。
- **in-place rerun（契约 B）**: rerun/edit-recalculate 复用同一任务目录与远端目录，`attempt+1`（CAS 单事务），旧 attempt 回执归档至 `WORK/00_RUNTIME/attempts/<N>/`（`_archive_attempt_receipts` + `_archive_previous_resume_source`）；`_RERUN_STABLE_FILES`（`input.xyz`/`input_source.json`/`task.json`/`job.json`）跨 attempt 保留，其余清空。
- **Attempt chain (ACP 2.0)**: terminal CAS writes `publication_pending=True`；`_resume_pending_publications()` retries publication only (never the executor). Manager seams: `attempt_completion`（`services_completion_bootstrap.build_attempt_completion_service`）and `attempt_publication`（`calculations/attempt_publication.AttemptResultPublication`）；frozen attempt group（`WORK/00_RUNTIME/frozen/` + `frozen_group.json`）is published by `acp_services.runtime.attempt_freeze` from the spec built in `calculations/attempt_freeze.py`.
- **Runtime 用例单一 owner（B05-1，todo 25；B05-7，todo 77）**: 控制意图（submit/pause/unpause/cancel/continue/rerun/edit）**归 `JobManager` 单一 owner**；API（`src/acp/api/v1_routes.py`）与 CLI（`src/acp/cli.py`）的提交/控制入口一律经 `acp_services.runtime.commands.RuntimeCommands` 用例，每个用例经注入端口（`JobSubmissionPort`/`JobControlPort`/`JobEditPort`/`JobRegistrationPort`）**恰好转发一次**到 manager/registration，**不得**直调 store 或 manager 方法。store/schema、plan/step 契约与参数转换的实现已于 todo 77 迁入 `acp_services` 并移除对应注入；控制状态机仍由 manager 单点拥有（不是第二状态机、不双写）。`tests/test_services_runtime_usecases.py` 守护。

## ANTI-PATTERNS
- **Bare `except Exception:`**: 137 occurrences in `scheduler/`（top-level 84：`manager.py` 53、`runner.py` 5；其余在 remote/）— most logged, some silently swallow; see root ANTI-PATTERN #8
- **SQLite connection sprawl**: No connection pooling or centralized DB manager. Each store class opens its own connection to the same DB file
- **Event/Provenance split ambiguity**: JobEventLog (JSONL file) and Provenance (dataclass + store) overlap in purpose. Event sourcing vs structured provenance not clearly separated
- **`__all__` drift risk**: `__init__.py` selective re-exports vs each module's own `__all__`. Easy to forget adding new exports
- **`from __future__ import annotations` in dataclasses with `asdict()`**: Known mypy issue — `asdict()` fails on field types that are `str | None` under PEP 604. Some files use `typing.Optional[str]` workaround inconsistently
- **No TTL/purging for jobs**: Jobs accumulate indefinitely in SQLite (local_cleanup handles run-dir disk only, not the DB)
- **Forwarder re-implementation**: `store.py`/`migrations.py`/`projects.py`/`molecule_groups.py`/`naming.py` are identity re-exports of `acp_services.*`; adding SQL, schema logic, or a second store class there violates root ANTI #30 and `tests/test_services_migration_equivalence.py`
- **Deleting compatibility paths**: old `acp.scheduler.store`/`migrations`/`projects` imports must stay available (callers in `public_shim_inventory.md`); do not remove them for line count
