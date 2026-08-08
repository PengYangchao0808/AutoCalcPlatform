# scheduler/ — Phase 2 Task Scheduler

## OVERVIEW
Job queue, persistence, and background execution for ACP calculations. Receives JobSpec from CLI or API, persists to SQLite, runs as subprocess via `acp run <workflow>`, captures artifacts/provenance. 13 files, ~2700 lines.

## STRUCTURE
```
scheduler/
├── __init__.py           # Re-exports all public symbols (18+ items), 37 lines
├── jobs.py               # Core data models: JobStatus (enum), JobSpec, JobRecord, 126 lines
├── manager.py            # JobManager — submit, cancel, list, work_dir_of, shutdown, 276 lines
├── runner.py             # JobRunner — subprocess execution, stdin materialization, artifact capture, 593 lines
├── store.py              # JobStore — SQLite CRUD: create/update/get/list/delete/counts, 228 lines
├── stage_tasks.py        # StageTask store + StagePlan providers + StageTaskObserver (polls work dir), 403 lines
├── provenance.py         # Provenance dataclass, ParserRegistry, compute_input_hash, audit event building, 222 lines
├── events.py             # JobEventLog — append/read/tail on JSONL event file per job, 79 lines
├── artifacts.py          # Artifact + ArtifactRegistry (SQLite), capture_stage_artifacts, 263 lines
├── files.py              # build_manifest (work dir listing) + resolve_safe (path traversal guard), 65 lines
├── logs.py               # read_log_tail + read_log_range from job log files, 37 lines
├── projects.py           # ProjectManager — create/get/list/update/delete projects in SQLite, 209 lines
└── migrations.py         # Schema migration: migrate(), get_schema_version, column checks, 164 lines
```

## WHERE TO LOOK
| File | Key contents |
|------|-------------|
| `jobs.py` | JobStatus (pending/running/completed/failed/cancelled), JobSpec (workflow/input/params), JobRecord (status+timestamps+metadata) |
| `jobs.py` helpers | `censo_preset_from_method` / `censo_solvent_from_method` / `censo_ewin_from_method` / `xtbmd_method_flags` (+ `_as_bool`) — shared CLI-flag resolution used by both `runner.py` and `remote/script_gen.py` (E7 parity; add new xtbmd flags here, not in either file; boolean method values are string-tolerant) |
| `manager.py` | JobManager — entry point. `submit()` creates job → stores via JobStore → spawns thread. `cancel()` sets event. `_run_job()` callback invokes JobRunner. Re-queues orphaned active jobs on startup. |
| `runner.py` | JobRunner — biggest file. `run()` builds CLI cmd, spawns subprocess, monitors via `_monitor()`, observes stage state via `StageTaskObserver`, captures artifacts via `capture_stage_artifacts()`, stores provenance. `materialize_job_input()` writes SMILES/XYZ to disk. `_run_fake()` for testing. |
| `store.py` | JobStore — SQLite persistence for JobRecord. Schema init, CRUD, project filtering, count aggregation. |
| `stage_tasks.py` | StageTask/StagePlan dataclasses, StagePlanProvider protocol (per workflow: mechanism/ensemble/energy/xtbmd_censo_energy/simple), StageTaskStore (SQLite CRUD), StageTaskObserver (polls work dir for `.stage_*` files, mirrors to DB). |
| `provenance.py` | Provenance dataclass (input_hash, command_line, wall_time, parser_results), ParserRegistry (type→callable), `compute_input_hash()`, `build_provenance_for_job()`. |
| `artifacts.py` | Artifact (type/path/checksum/context), ArtifactRegistry (SQLite CRUD by job/task/type), `capture_stage_artifacts()` scans `.stage_*` directories, computes SHA-256 checksums. |
| `events.py` | JobEventLog — append-only JSONL per job ID. Used by manager for audit trail. |
| `projects.py` | ProjectManager — project metadata CRUD, `ensure_default_project()` auto-creates. Tags and settings as JSON blobs. |
| `migrations.py` | `migrate(db_path)` runs schema version check + pending migrations. Auto-called by JobStore/ProjectManager init. |

## CONVENTIONS
- **Type annotations**: PEP 604 (`X | None`) with `from __future__ import annotations` (matches `acp/` style)
- **Docstrings**: Compact, one-line where possible. Module-level docstrings in all files
- **`_utc_now_iso()`**: Private helper copied across 9 modules (no shared utility)
- **SQLite**: All stores (JobStore, StageTaskStore, ArtifactRegistry, ProjectManager) create their own SQLite connections. Schema via `sqlite3` directly, no ORM
- **`__all__`**: Every module exports `__all__`. `__init__.py` aggregates selectively
- **Threading**: Job lifecycle via `threading.Thread` + `threading.Event` (cancel signal). No asyncio in scheduler
- **Event files**: Per-job JSONL event log at `<work_dir>/events.jsonl`. Read via JobEventLog, not the SQLite DB
- **`.stage_*` convention**: Stage task state → JSON files in work dir. StageTaskObserver polls these files

## ANTI-PATTERNS
- **`_utc_now_iso()` copy-pasted**: Same 3-line helper defined in 9 modules (`events.py`, `jobs.py`, `manager.py`, `migrations.py`, `projects.py`, `provenance.py`, `runner.py`, `stage_tasks.py`, `store.py`). Candidate for `acp.core.utils`
- **Bare `except Exception:`**: Present in 2+ modules (manager, runner). Silently swallows errors
- **SQLite connection sprawl**: No connection pooling or centralized DB manager. Each store class opens its own connection to the same DB file
- **Event/Provenance split ambiguity**: JobEventLog (JSONL file) and Provenance (dataclass + store) overlap in purpose. Event sourcing vs structured provenance not clearly separated
- **Runner coupling**: JobRunner directly imports from `acp.workflows.conformer`, `acp.backends.registry`, `acp.core.models` — tight coupling to parent package internals
- **`__all__` drift risk**: `__init__.py` selective re-exports (18 symbols) vs each module's own `__all__`. Easy to forget adding new exports
- **`from __future__ import annotations` in dataclasses with `asdict()`**: Known mypy issue — `asdict()` fails on field types that are `str | None` under PEP 604. Some files use `typing.Optional[str]` workaround inconsistently
- **No TTL/purging**: Jobs accumulate indefinitely in SQLite. No cleanup for completed/failed jobs
