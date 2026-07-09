ACP Workbench v2 -- Project-Centered Web Platform Design Plan
============================================================

**Status:** Design draft -- grounded in current `acp/` code (2026-06-26).  
**Supersedes:** `docs/ACP_Frontend_Target_Implementation_Plan.md` and the Phase 2 section of `docs/xTBridge_Implementation_Plan.md`.  
**Does not migrate to:** AiiDA, FireWorks, or QCFractal. ACP keeps its own core; it borrows provenance/schema/queue ideas from those projects.

---

## 1. Executive Summary

ACP Workbench v2 is repositioned from a "job submission dashboard" to a **browser-based operating system for computational-chemistry mechanism research**. The unit of interaction is no longer a single job but a **Project**, which contains Jobs, each composed of StageTasks, each producing Artifacts with full provenance.

This plan defines:

1. A four-layer data model: **Project → Job → StageTask → Artifact**.
2. A unified entry point -- the **Molecule Studio** -- where users draw/import molecules, configure workflows, and inspect results.
3. A stable `/api/v1` backend with Project/Job/Task/Artifact endpoints.
4. A storage layer with content-addressed artifacts and a structured provenance record.
5. A phased implementation roadmap (**10 sprints**) starting from v1 stabilization and ending with mechanism workflows.

The golden rule: **do not build polished UI before the underlying JobSpec / ResultSchema / ArtifactRegistry / Provenance model is unified.** The web frontend consumes stable backend capabilities; it is not the source of truth.

A second rule: **observe before controlling.** Today's real workflows run as `python -m acp.cli run <workflow>` subprocesses; the API polls `state.json` for progress. v2 first hardens this observational model, then optionally moves toward in-process stage control.

---

## 2. Guiding Principles (Non-Negotiable)

| # | Principle | Rationale |
|---|-----------|-----------|
| 1 | **Project is the primary unit.** A project owns input molecules, parameter templates, job history, and analysis notebooks. Jobs are cheap to create and compare inside a project. | Matches how researchers actually work (a paper/reaction/system = one project). |
| 2 | **Artifact + Provenance before UI polish.** Every file, parsed quantity, and figure must be content-addressed and traceable before it is rendered. | Reproducibility and debugging come first; dashboards are secondary. |
| 3 | **StageTask observability first, with a path to control.** The scheduler initially *observes* stage-level status from the running workflow (via `state.json` and per-stage task files). True per-stage retry/cancel requires refactoring the subprocess boundary and is explicitly deferred until the observability layer is stable. | Matches today's `JobRunner` subprocess model; avoids promising capabilities the current architecture cannot deliver. |
| 4 | **Keep ACP core; borrow, do not migrate.** Use QCSchema field names, signac-style statepoint hashing, AiiDA-style provenance links, and FastAPI patterns, but keep execution in ACP code. | Avoids heavy external infrastructure while staying interoperable. |
| 5 | **Molecule Studio as the unified entry point.** All molecular input (draw, upload, paste, fetch) flows through one component and is resolved to a server-side canonical representation before any workflow runs. | Prevents divergent input handling across conformer/NMR/mechanism workflows. |

---

## 3. Current State Assessment

### 3.1 What exists today (verified by code read)

| Component | Location | State |
|-----------|----------|-------|
| Scheduler models | `src/acp/scheduler/jobs.py` | `JobSpec`, `JobRecord`, `JobStatus` -- job-level only, no stage tasks. |
| SQLite store | `src/acp/scheduler/store.py` | `jobs` table with `spec_json`/`result_json` blobs. No stage or artifact tables. |
| Job manager | `src/acp/scheduler/manager.py` | `ThreadPoolExecutor`, concurrency `max_running`, cancel via `threading.Event`. Requeues interrupted jobs on startup. |
| Workflow engine | `src/acp/core/workflow.py` | `WorkflowRunner` executes `Stage` callables sequentially and returns `WorkflowResult`. |
| State persistence | `src/acp/core/state.py` | `WorkflowState` tracks per-stage status/results in `state.json`; already has `stages` dict. |
| API routes | `src/acp/api/routes.py` | `/api/status`, `/api/backends`, `/api/workflows`, `/api/protocols`, `/api/jobs` CRUD + SSE events + file manifest/download. 12 routes, no versioning. |
| API schemas | `src/acp/api/schemas.py` | Pydantic models for jobs, backends, files. No Project/Task/Artifact models. |
| Frontend | `frontend/ACP_Workbench.html` | Single-file vanilla-JS dashboard. Dark theme, job queue, live log stream, file manifest. No project tree or visualization. |

### 3.2 Gaps relative to the v2 vision

1. **No Project concept.** Jobs are flat and global; there is no grouping, sharing, or project-level configuration.
2. **No StageTask entity.** `JobRecord.current_stage` is just a string; stage observability, retry, per-stage logs, and per-stage artifacts are not modeled. The current architecture supports stage *observation* via `state.json` but not stage *control*.
3. **No ArtifactRegistry.** Files are served by path from the job work directory; they are not content-addressed, typed, or parsed.
4. **No structured Provenance.** `JobRecord.result` is an opaque dict; there is no consistent provenance record across backends.
5. **No Molecule Studio.** Input is a free-text SMILES/file path; there is no drawing component, 3D preview, or canonicalization step.
6. **No visualization.** No 3D structure viewer, vibrational mode animation, or wavefunction isosurface rendering.
7. **No API versioning.** All routes live under `/api/*`; v2 introduces breaking schema changes that need `/api/v1`.
8. **Frontend is a single HTML file.** It cannot scale to multiple views (project tree, studio, viewer, inspector) without a build step.

---

## 4. Target Architecture

### 4.1 Four-layer data model

```
Project
  project_id (UUID)
  name, description, tags
  input_molecules[]          # canonical StructureRecords
  parameter_templates[]      # saved method/resource presets

  Job[]
    job_id (timestamp_seq_name, same as today)
    workflow (conformer | nmr | benchmark | mechanism | ...)
    input_hash (SHA256 of canonical input spec)
    status (queued | starting | running | cancelling | completed | failed | cancelled)
    work_dir

    StageTask[]
      task_id (UUID)
      stage_name (e.g. "crest_conformer_search")
      task_type (driver: energy | gradient | hessian | properties | opt | ts | irc)
      retry_count              # reserved for future control; initially 0
      pid: int | None          # subprocess PID, when observable
      stderr_summary: str      # tail of stderr for quick debugging
      state (pending | running | completed | failed | cancelled | skipped)
      exit_status (int, 0 = ok)
      started_at, completed_at, updated_at

      Artifact[]
        artifact_id (UUID)
        artifact_type (input_xyz | output_log | optimized_xyz | checkpoint | cube | ...)
        file_path (relative to work_dir)
        checksum (sha256:<hex>)
        size_bytes
        parser_status (pending | parsing | success | warning | error | skipped)
        metadata (parsed quantities, parser version, mime type)

      ResultSchema
        success, exit_status
        return_value (primary scalar/array)
        properties (dict of named quantities)
        error {error_type, error_message}
        schema_version

      Provenance
        input_hash, molecule_hash
        acp_version, backend_name, backend_version
        method, basis, solvent
        command_line, hostname, ncores, memory_gb
        wall_time_seconds, routine, creator
        schema_version
```

**Design notes:**

- `input_hash` follows the signac statepoint rule: **include every parameter that invalidates the output if changed** (molecule geometry hash, method, basis, charge, multiplicity, backend name/version, keyword settings). Exclude runtime-only data (hostname, wall time, date).
- `molecule_hash` is the canonical geometry/SMILES fingerprint (QCSchema-style).
- `Artifact.checksum` uses the OCI Content Descriptor format `sha256:<hex>` and is the storage key in the content-addressed artifact store.

### 4.2 Backend service layers

```mermaid
flowchart TB
    subgraph API["FastAPI /api/v1"]
        A[projects | jobs | tasks | artifacts | results | provenance]
        B[molecule | backends | protocols | events SSE | files]
    end
    subgraph MGR["Managers"]
        C[ProjectManager SQLite]
        D[JobManager ThreadPoolExecutor]
        E[StageTaskObserver mirror state.json]
    end
    subgraph RUN["Execution"]
        F[WorkflowRunner subprocess acp.cli run ...]
        G[WorkflowState state.json]
    end
    subgraph STORE["Storage"]
        H[ArtifactRegistry reference mode]
        I[ProvenanceStore]
        J[ParserRegistry]
    end
    subgraph QC["QC Layer"]
        K[Gaussian / ORCA / CREST / xTB]
    end
    API --> MGR
    MGR --> RUN
    RUN --> G
    E --> G
    RUN --> STORE
    RUN --> QC
```

**Important:** The `WorkflowRunner` box in v2 remains a **subprocess** for real workflows, exactly as it is today. The `StageTaskObserver` reads `state.json` and the new `stage_tasks/` directory and mirrors what it sees into the `stage_tasks` table. Control (per-stage retry/cancel) is a future option after the observability layer is proven.

### 4.3 API v1 surface

All new routes are prefixed `/api/v1`. Existing `/api/*` routes remain **fully operational** until the v2 frontend is switched on; mutation endpoints (`POST /api/jobs`, `POST /api/jobs/{id}/cancel`) are **not** removed or made read-only in the first release. Deprecation is a separate later step.

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/v1/status` | Service health + queue counts |
| GET | `/api/v1/backends` | Backend availability + capabilities |
| GET | `/api/v1/workflows` | Workflow metadata |
| GET | `/api/v1/protocols` | Protocol list + descriptions |
| **Projects** |
| GET/POST | `/api/v1/projects` | List / create projects |
| GET/PATCH/DELETE | `/api/v1/projects/{id}` | CRUD |
| GET | `/api/v1/projects/{id}/jobs` | Jobs within a project |
| GET | `/api/v1/projects/{id}/molecules` | Molecules in project library |
| POST | `/api/v1/projects/{id}/molecules` | Add molecule to library |
| **Jobs** |
| POST | `/api/v1/jobs` | Submit job. `project_id` is optional; missing values are assigned to the default `"Uncategorized"` project. |
| GET | `/api/v1/jobs` | List jobs, filter by project/status |
| GET/POST | `/api/v1/jobs/{id}` / `/cancel` | Get / cancel |
| GET | `/api/v1/jobs/{id}/tasks` | Stage tasks |
| GET | `/api/v1/jobs/{id}/events` | SSE stream |
| **StageTasks** |
| GET | `/api/v1/tasks/{task_id}` | Task details |
| POST | `/api/v1/tasks/{task_id}/retry` | Retry failed/cancelled task |
| POST | `/api/v1/tasks/{task_id}/cancel` | Cancel running task |
| GET | `/api/v1/tasks/{task_id}/logs` | Stdout/stderr tail |
| **Artifacts** |
| GET | `/api/v1/artifacts/{artifact_id}` | Metadata |
| GET | `/api/v1/artifacts/{artifact_id}/download` | Download file |
| GET | `/api/v1/artifacts/{artifact_id}/content` | Inline content for small text files |
| GET | `/api/v1/jobs/{id}/artifacts` | List artifacts by job |
| GET | `/api/v1/tasks/{task_id}/artifacts` | List artifacts by task |
| **Molecule Studio** |
| POST | `/api/v1/molecule/resolve` | SMILES/InChI/XYZ → canonical StructureRecord |
| POST | `/api/v1/molecule/embed` | Generate 3D conformer server-side (RDKit) |
| GET | `/api/v1/molecule/{id}/xyz` | Return XYZ for a library molecule |

### 4.4 Frontend architecture

The frontend becomes a small React/Vue single-page application (build step added to `pyproject.toml`). The initial layout is a 3-pane workbench:

```
+-----------------------------------------------------------+
| Top bar: project selector, service status, user settings  |
+-------------+------------------------------+--------------+
|             |                              |              |
|  Project    |      Main Workspace          |  Inspector   |
|  Tree       |      (tabbed views)          |  / Details   |
|             |                              |              |
|             |  - Molecule Studio           |              |
|             |  - Job Queue                 |              |
|             |  - Stage Timeline            |              |
|             |  - 3D Structure Viewer       |              |
|             |  - Wavefunction Viewer       |              |
|             |  - Artifact Browser          |              |
|             |  - NEB/IRC Builder (Phase 4+)|              |
|             |                              |              |
+-------------+------------------------------+--------------+
```

**Five primary application areas (from user vision):**

1. **Project & Data Management** -- project tree, molecule library, parameter templates, tags.
2. **Molecule Studio** -- 2D drawing, 3D preview, import/export, canonicalization.
3. **Job Control Center** -- submit, queue, stage timeline, retry/cancel, resource presets.
4. **Results & Analysis** -- tables, charts (energy diagrams, Boltzmann populations), artifact browser.
5. **Advanced Visualization** -- 3Dmol.js for structures/vibrations, cube isosurfaces, NEB/IRC paths.

---

## 5. Subsystem Designs

### 5.1 Project Manager

A new `ProjectManager` in `src/acp/scheduler/projects.py` backed by a SQLite `projects` table.

**Schema:**

```sql
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    tags TEXT,                       -- JSON list
    run_root TEXT NOT NULL,          -- project-owned directory
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**Behavior:**

- Creating a project creates `run_root/<project_id>/`.
- Jobs submitted without a `project_id` are placed in a default project named `"Uncategorized"`.
- Deleting a project deletes its directory only if the user passes `?delete_data=true`; otherwise only metadata is removed.
- Project-level settings (default protocol, default resources) are stored as JSON in a `settings` column.

**Default project behavior:**

- On first `JobManager` startup, ensure a default project `"Uncategorized"` exists and record its `project_id`.
- `POST /api/v1/jobs` without a `project_id` auto-assigns the default project before the `JobRecord` is created.
- The CLI `acp run conformer ...` (when invoked directly) also maps to the default project so that API-created and CLI-created jobs share the same indexing rules.
- Jobs can be moved between projects later via `PATCH /api/v1/jobs/{id}`.

### 5.2 StageTask Observability (Control Deferred)

The v2 plan intentionally does **not** promise per-stage retry or cancellation in the first implementation. The reason is architectural: today's real workflows run as `python -m acp.cli run <workflow>` subprocesses launched by `JobRunner._run_subprocess()` (`src/acp/scheduler/runner.py:118-124`). The API has no in-process hook into individual `Stage.func` calls; it only polls `state.json` and emits `stage.*` events.

Therefore, the first StageTask milestone is **observability**: mirror the runtime stage state into a queryable `stage_tasks` table and expose it via `/api/v1/jobs/{id}/tasks` and SSE.

#### StageTask table

```sql
CREATE TABLE stage_tasks (
    task_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    task_type TEXT,
    state TEXT NOT NULL,
    exit_status INTEGER,
    retry_count INTEGER DEFAULT 0,
    pid INTEGER,
    stderr_summary TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    result_json TEXT,
    provenance_json TEXT
);
```

#### StageTask lifecycle file layout

In addition to the existing `state.json`, the subprocess writes per-stage JSON files under the job work directory:

```
{work_dir}/
  state.json
  events.jsonl
  stage_tasks/
    crest/
      {task_id}.started.json
      {task_id}.completed.json
    gaussian-opt/
      {task_id}.started.json
      {task_id}.failed.json
```

This is the same pattern used by Snakemake (`.snakemake/metadata/{file}`) and GitHub Actions (`TimelineRecord`): the worker writes small, structured state files; the API mirrors them.

#### StageTaskObserver

A new `StageTaskObserver` runs in the API process (inside `JobRunner._monitor()` or as a lightweight polling thread):

1. On job start, create `stage_tasks` rows in `pending` state for each stage returned by the workflow's `StagePlanProvider`.
2. Poll `{work_dir}/stage_tasks/**/*.json` every second.
3. On each new/updated file, update the matching `stage_tasks` row and emit a `stage.*` event to `events.jsonl`.
4. On subprocess exit, mark any unfinished rows as `failed` or `cancelled` based on the final job status.

#### StagePlanProvider

Because some workflows (benchmark, mechanism) cannot statically enumerate every stage, each workflow module provides a `StagePlanProvider`:

```python
class StagePlanProvider(Protocol):
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]: ...
```

- `conformer` returns the 7 fixed stages.
- `nmr` returns its fixed stages; if it first runs a conformer pre-step, that step is listed.
- `benchmark` returns a fixed "loop controller" stage plus placeholder rows for each protocol; actual rows are created at runtime.
- `mechanism` returns a high-level plan (reactant-opt, product-opt, neb, ts-opt, irc, ...); dynamic stages are appended at runtime.

If a workflow has no provider, the observer falls back to discovering stages purely from `state.json`.

#### Retry/cancel scope

- **Retry:** Not implemented in the observability phase. `retry_count` is reserved and stays at `0`. A future "control" phase can add retry by re-invoking a single stage with recovered input artifacts.
- **Cancel:** Whole-job cancel remains the only cancel path (`POST /api/jobs/{id}/cancel`). Per-stage cancel requires the in-process hook and is deferred.

### 5.3 Artifact Registry

Artifacts are registered automatically after each stage completes. Two storage modes:

1. **Reference mode (default):** artifact lives in the job work directory; the registry stores metadata + checksum. Keeps current file layout.
2. **Content-addressed mode (optional, for sharing/deduplication):** file is copied to `.acp_artifacts/objects/sha256/aa/bb/cc...` and referenced by checksum. Enables cross-project artifact reuse.

#### Artifact capture strategy

Because the API does not know which files a stage produces until after it runs, artifact registration uses a **stage workdir convention plus snapshot diff**:

- Each stage writes outputs into a predictable subdirectory: `{work_dir}/stage_tasks/{stage_name}/`.
- Before a stage starts, the observer records a file manifest snapshot of that subdirectory.
- After the stage completes, the observer computes the diff, registers new files as `Artifact` rows, and computes checksums.
- Special files (Gaussian `.chk`, CREST ensemble, ORCA `.gbw`) are tagged by extension → `artifact_type` mapping.
- A configurable ignore list skips scratch files (`*.tmp`, `*.pid`, `core.*`, `slurm-*`).

This avoids guessing artifact ownership while keeping the registry accurate even for multi-output stages such as CREST and benchmark sub-runs.
```sql
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES stage_tasks(task_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    checksum TEXT NOT NULL,          -- sha256:<hex>
    size_bytes INTEGER,
    parser_status TEXT DEFAULT 'pending',
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
```

**Artifact types (extensible enum):**

- `input_xyz`, `input_gjf`, `input_sdf`
- `optimized_xyz`, `final_ensemble`
- `gaussian_log`, `orca_log`, `crest_output`, `xtb_output`
- `checkpoint` (`.chk`, `.rwf`)
- `cube` (electron density, MO, ESP)
- `shermo_out`, `isostat_out`
- `nmr_shielding`, `nmr_report`
- `trajectory` (IRC/NEB path)

### 5.4 Provenance Store

Every `StageTask` owns one `Provenance` record. The schema borrows field names from QCSchema and AiiDA:

```python
@dataclass
class Provenance:
    input_hash: str
    molecule_hash: str | None
    acp_version: str
    backend_name: str
    backend_version: str
    method: str
    basis: str | None
    solvent: str | None
    command_line: str
    hostname: str
    ncores: int | None
    memory_gb: float | None
    wall_time_seconds: float | None
    routine: str | None
    creator: str | None
    schema_version: str = "1.0"
```

**Key decisions:**

- `input_hash` is computed from canonical JSON (sorted keys, no whitespace) with SHA256.
- `backend_version` is captured by running `<binary> --version` once and caching, or by parsing the output log.
- `command_line` stores the exact invocation string for reproducibility.
- `schema_version` enables future migrations without ambiguity.

### 5.5 Schema Migrations

The existing `acp_jobs.db` is created via `CREATE TABLE IF NOT EXISTS` with no versioning. v2 introduces a lightweight, hand-rolled migration system in `src/acp/scheduler/migrations.py`:

```python
_MIGRATIONS = [
    {
        "id": "001_add_projects_and_stage_tasks",
        "description": "Add projects, stage_tasks, artifacts tables",
        "sql": "...",
    },
]

def migrate(db_path: Path) -> int:
    """Apply pending migrations, return count applied."""
```

**Design choices:**

- Use a `_schema_migrations` meta-table (`id`, `applied_at`).
- Keep SQL portable (avoid SQLite-only syntax) so the same migrations can run on PostgreSQL later.
- Apply migrations automatically inside `JobStore.__init__()` after `_init_schema()`.
- Migrations are idempotent: running twice does nothing.

**Future:** If ACP later adopts PostgreSQL, replace the hand-rolled runner with `yoyo-migrations` (or Alembic) while keeping the same DDL.

### 5.6 Molecule Studio

The Molecule Studio is the **single entry point for molecular input**.

**2D drawing component:** Ketcher v3.x (Apache-2.0, React, supports V3000 MOLfiles). It is loaded as an iframe or React component.

**Input pipelines:**

| User action | Frontend | Backend |
|-------------|----------|---------|
| Draw in Ketcher | Get MOLfile/SMILES | `POST /api/v1/molecule/resolve` → canonical SMILES + InChI |
| Paste SMILES | Validate format | Resolve → 2D depiction + InChI |
| Upload XYZ/SDF | Send file | Detect format, store in project library |
| Fetch by name/CID | User enters name | Optional: PubChem lookup (future) |

**3D preview:** After resolution, the frontend requests server-side embedding (`POST /api/v1/molecule/embed`) and displays the XYZ with **3Dmol.js**.

**Library molecule model:**

```python
@dataclass
class LibraryMolecule:
    molecule_id: str          # UUID
    project_id: str
    name: str
    smiles: str
    inchi: str | None
    inchikey: str | None
    formula: str
    charge: int
    multiplicity: int
    xyz: str | None           # canonical 3D conformer
    source: str               # "ketcher" | "upload" | "smiles"
    created_at: str
```

### 5.6 3D Structure & Wavefunction Viewer

**Library selection (verified):**

| Library | Role | Status |
|---------|------|--------|
| **3Dmol.js** | Primary 3D viewer (structures, vibrations, surfaces) | Active v2.5.5, 13 MB. `vibrate()` confirmed real. `addIsosurface()` supports cube files. No atom dragging. |
| **NGL Viewer** | Advanced macromolecular / trajectory viewing | Stalled; skip unless trajectory needs exceed 3Dmol.js. |
| **Mol*** | Heavy-duty visualization | Active but 73 MB; defer until needed. |
| **Kekule.js** | 2D/3D combined | SKIP -- outdated, poor maintenance. |

**Capabilities per phase:**

- **Phase 1 (now):** display XYZ/SDF, hover atom info, rotate/zoom, screenshot.
- **Phase 2:** animate vibrational modes from frequency logs (using `vibrate()`), display conformer ensemble with energy labels.
- **Phase 3:** render cube isosurfaces (HOMO/LUMO, electron density, ESP) from Gaussian/ORCA `.cube` files.
- **Phase 4:** NEB/IRC path animation, TS mode visualization.

**NCI/RDG plots:** require server-side pre-computation (Promolecular density + RDG) and return a grid or 2D scatter; not rendered client-side from scratch.

### 5.7 NEB / IRC / TS Builder (Phase 4)

Mechanism workflows are added after conformer/NMR workflows are stable. The Molecule Studio will include:

- **Reactant/Product alignment:** RMSD-based atom mapping between two library molecules.
- **Interpolation:** generate initial guess geometries along a reaction coordinate.
- **NEB builder:** configure images, spring constants, interpolation method.
- **TS optimization:** submit `Opt=TS` with `CalcFC`.
- **IRC verification:** run IRC in both directions, map endpoints back to reactant/product.
- **Energy profile:** plot reaction coordinate vs. free energy.

These are **StageTask types** (`neb`, `ts_opt`, `irc_forward`, `irc_reverse`) that plug into the existing stage queue.

---

## 6. Migration Path from Current ACP

The existing implementation is preserved and extended; there is no rewrite.

| Current | Migration |
|---------|-----------|
| `JobSpec` / `JobRecord` | Keep; add optional `project_id` and `input_hash`. |
| `JobStore` table | Keep; add columns `project_id`, `input_hash`. Create `projects`, `stage_tasks`, `artifacts` tables. |
| `JobManager` | Extend to mirror stage state from `state.json`/`stage_tasks/` into the database; whole-job cancel remains the only cancel path initially. |
| `WorkflowRunner` | No change in v2 observability phase. A future "control" phase adds yield/callback hooks for per-stage retry/cancel. |
| `WorkflowState` (state.json) | Keep as ground-truth runtime state; add `stage_tasks/` directory as a structured, per-stage event stream. |
| `/api/*` routes | Remain fully operational (read + write) until the v2 frontend is switched on. Deprecate mutations only after v2 frontend is default. |
| `/api/v1/*` routes | New authoritative API. v2 frontend uses these. |
| `frontend/ACP_Workbench.html` | Keep as legacy fallback; new frontend served from `frontend/v2/` build output. |

**Data migration:**

- Existing jobs without a project are assigned to the default `"Uncategorized"` project.
- Existing `result_json` blobs are parsed; if they contain `state.stages`, retroactively create `StageTask` rows.
- Existing files in job directories are scanned and registered as artifacts with checksums.

---

## 7. Implementation Sprints

### Sprint 0 -- Workbench v1 Stabilization (1 week)

Most of the originally feared v1 stabilization issues are already fixed and tested:

| Issue | Status | Key file | Test |
|-------|--------|----------|------|
| `state.json` discovery | **FIXED** | `runner.py:34-58` | `test_acp_scheduler.py:138-161` |
| Benchmark command mapping | **FIXED** | `runner.py:215-216` | `test_acp_scheduler.py:122-135` |
| Queued job cancel | **FIXED** | `manager.py:95-122` | `test_acp_scheduler.py:164-179`, `test_acp_api.py:123-144` |
| Backend capability detection | **FIXED** | `capabilities.py:53-117`, `routes.py:191-211` | `test_acp_api.py:38-60` |
| Path clamping (`output_dir`) | **FIXED** | `manager.py:128-149` | `test_acp_scheduler.py:182-196` |

Remaining Sprint 0 tasks:

1. **Path traversal test gap** -- Add `test_resolve_safe_rejects_traversal` for `resolve_safe()` at `files.py:51`.
2. **Frontend hardening** -- Refactor `updateQueueChips()` in `ACP_Workbench.html` to escape interpolated values or use DOM `textContent`.
3. **Regression pass** -- Run full test suite and a real `conformer` API job end-to-end.

### Sprint 1 -- Project + Migration Foundation (2 weeks)

- Add `src/acp/scheduler/migrations.py` with `_schema_migrations` meta-table and linear migration list.
- Add `ProjectManager`, `projects` table, default `"Uncategorized"` project.
- Extend `JobSpec`/`JobRecord` with `project_id`, `input_hash`.
- Add `project_id` column to `jobs` table via migration.
- Auto-assign missing `project_id` to the default project in `JobManager.submit()` and CLI entry points.
- Tests: migration idempotency, project CRUD, default-project assignment, CLI/API consistency.

### Sprint 2 -- StageTask Observability (2 weeks)

- Add `StageTask` dataclass + `stage_tasks` table via migration.
- Add `StagePlanProvider` protocol and implementations for `conformer`, `nmr`, `benchmark`, `fake`.
- Add `StageTaskObserver` that polls `{work_dir}/stage_tasks/**/*.json` and mirrors to the database.
- Modify `WorkflowRunner`/`Stage` functions to write per-stage `.started.json`/`.completed.json`/`.failed.json` files (minimal hooks, no pipeline rewrite).
- Expose `GET /api/v1/jobs/{id}/tasks` and per-task SSE events.
- Tests: stage timeline accuracy, observer idempotency, dynamic stage creation for benchmark.

### Sprint 3 -- Artifact Registry (Reference Mode) (2 weeks)

- Create `ArtifactRegistry`, `artifacts` table via migration.
- Implement reference-mode registration only; defer content-addressed storage.
- Implement stage workdir convention + snapshot-diff capture strategy.
- Define artifact type mapping by extension and backend output conventions.
- Add `GET /api/v1/artifacts/{id}`, `/download`, `/content`, and `/api/v1/jobs/{id}/artifacts`.
- Tests: artifact registration after each stage, checksum computation, extension mapping, ignored scratch files.

### Sprint 4 -- Provenance + ResultSchema (2 weeks)

- Define `Provenance` dataclass and capture logic in `JobRunner`/`StageTaskObserver`.
- Implement `input_hash` canonicalization utility (canonical JSON + SHA256).
- Add parser status enum and stub parser registry.
- Store parsed quantities from log files into `Artifact.metadata`.
- Tests: provenance fields populated, input_hash stable across equivalent specs, parser status transitions.

### Sprint 5 -- `/api/v1` Backend + Compatibility Bridge (2 weeks)

- Add `/api/v1/projects`, `/api/v1/jobs`, `/api/v1/tasks`, `/api/v1/artifacts` routes.
- Add SSE streams for projects and tasks.
- Add `/api/v1/molecule/resolve` and `/api/v1/molecule/embed`.
- Keep `/api/*` fully operational; do not remove or read-only mutation endpoints.
- Tests: full `/api/v1` coverage with TestClient, backward-compat checks for `/api/*`.

### Sprint 6 -- Molecule Studio Frontend (2 weeks)

- Set up frontend build (Vite + React) in `frontend/v2/`.
- Integrate Ketcher for 2D drawing.
- Implement SMILES/upload resolution flow via `/api/v1/molecule/resolve`.
- Add 3Dmol.js preview after server-side embed.
- Project tree and molecule library sidebar.

### Sprint 7 -- Job Control + Stage Timeline (2 weeks)

- Job submission form wired to `/api/v1/jobs`.
- Stage timeline component (per-task status, logs, no retry yet).
- Live log stream via SSE.
- Queue chips and project-level job list.

### Sprint 8 -- Results & Artifact Browser (2 weeks)

- Artifact browser (tree view, checksum, download).
- Parsed results tables (energies, Boltzmann weights).
- Simple energy diagram plot.
- NMR report viewer (Phase 3 result consumption).

### Sprint 9 -- Advanced Visualization (2 weeks)

- Vibrational mode animation in 3Dmol.js.
- Cube isosurface rendering (HOMO/LUMO, density).
- Conformer ensemble viewer with energy slider.

### Sprint 10 -- Mechanism Workflows + NEB/IRC Builder (3 weeks)

- Add `mechanism` workflow and `StagePlanProvider`.
- Reactant/product alignment and interpolation.
- NEB, TS opt, IRC stage tasks (observability first).
- Energy profile plot and TS mode viewer.

**Future beyond Sprint 10:** True StageTask-level retry/cancel (control mode) after the observability layer is stable and the team decides to refactor `JobRunner` from subprocess to in-process/hybrid dispatch.

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| RDKit.js maintenance limbo | Molecule Studio 2D/3D client-side features break | Use Ketcher for 2D; always do 3D embedding server-side with RDKit Python. |
| 3Dmol.js cannot drag atoms | Limits interactive TS building | Build TS guess server-side; 3Dmol.js is display-only for now. |
| SQLite contention at scale | Scheduler becomes bottleneck | Design tables with row-level state; keep option to swap `JobStore` for PostgreSQL later without changing models. |
| Content-addressed storage fills disk | Deduplication saves space but needs GC | Implement reference counting + periodic orphan sweep. |
| Existing `/api/*` consumers break | External scripts, the current HTML | Keep `/api/*` mutations live until v2 frontend is default; deprecate only after switch-over. |

---

## 9. Appendix A -- Schema Definitions

### A.1 Project

```python
class Project(BaseModel):
    project_id: str          # UUID
    name: str
    description: str = ""
    tags: list[str] = []
    run_root: str
    settings: dict = {}
    created_at: str
    updated_at: str
```

### A.2 StageTask

```python
class StageTask(BaseModel):
    task_id: str
    job_id: str
    stage_name: str
    task_type: str | None      # energy | gradient | hessian | properties | opt | ts | irc
    state: str                 # pending | running | completed | failed | cancelled | skipped
    exit_status: int = 0
    retry_count: int = 0       # reserved; 0 until control mode is implemented
    pid: int | None = None
    stderr_summary: str | None = None
    started_at: str | None
    completed_at: str | None
    updated_at: str
    result: ResultSchema | None
    provenance: Provenance | None
```

### A.3 ResultSchema

```python
class ResultSchema(BaseModel):
    schema_name: str = "acp_result"
    schema_version: str = "1.0"
    success: bool
    exit_status: int = 0
    return_value: float | list[float] | None
    properties: dict = {}
    error: dict | None         # {error_type, error_message}
```

### A.4 Artifact

```python
class Artifact(BaseModel):
    artifact_id: str
    task_id: str
    job_id: str
    artifact_type: str
    file_path: str
    checksum: str              # sha256:<hex>
    size_bytes: int
    parser_status: str         # pending | parsing | success | warning | error | skipped
    metadata: dict = {}
    created_at: str
```

---

## 10. Appendix B -- Library Selection Matrix

| Library | Version | License | Size | Role | Decision |
|---------|---------|---------|------|------|----------|
| Ketcher | 3.15.0 | Apache-2.0 | ~ few MB | 2D drawing | **Adopt** |
| 3Dmol.js | 2.5.5 | BSD-3 | ~13 MB | 3D viewer, vibrations, surfaces | **Adopt** |
| NGL | stalled | MIT | ~ | Trajectories | Defer |
| Mol* | 5.10.1 | MIT | ~73 MB | Advanced viz | Defer |
| Kekule.js | old | Apache-2.0 | ~ | 2D/3D | **Skip** |
| RDKit.js | maintenance | BSD | ~13 MB WASM | Client-side chem | Avoid; use Python RDKit server-side |

---

## 11. Appendix C -- Relationship to Existing Documents

- **`docs/ACP_Frontend_Target_Implementation_Plan.md`** -- Superseded. Its Phase 2 goals are folded into Sprints 5-9 here, with the important correction that backend data models (Project/StageTask/Artifact/Provenance) and schema migrations are built before any new frontend.
- **`docs/xTBridge_Implementation_Plan.md`** -- Superseded for the API/scheduler portions. The cross-program bridge ideas remain valid but are out of scope until the Workbench v2 core is complete.
- **`README.md`** -- Will be updated after Sprint 5 to document `/api/v1` and the new frontend build.

---

## 12. Open Questions (To Resolve Before Sprint 1)

1. **Frontend framework:** React (recommended, Ketcher has React wrappers) or Vue? Decision needed before Sprint 6.
2. **Build integration:** Should the frontend be bundled into the Python wheel (via `setuptools` data files) or built separately and copied into `frontend/v2/dist/`? Recommended: build in CI and ship static files.
3. **Authentication:** Is multi-user support required in v2? If yes, add `user_id` to projects/jobs and a simple API-key auth layer in Sprint 1. If no, defer.
4. **External database:** Should PostgreSQL be an optional backend for `JobStore`/`ProjectManager` from the start? Recommended: keep SQLite default, abstract store interface so PostgreSQL is a later drop-in.
5. **Molecule naming/lookup:** Should the Molecule Studio support PubChem/ChemSpider lookup? Recommended: defer to post-Sprint 7.
6. **StageTask control mode:** After Sprint 2, do we want to invest in refactoring `JobRunner` to support per-stage retry/cancel (in-process/hybrid dispatch), or stay observational for the foreseeable future?
