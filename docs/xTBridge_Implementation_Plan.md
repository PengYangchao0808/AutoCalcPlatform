# xTBridge Implementation Plan

Generated: 2026-06-25

## 1. Executive Summary

xTBridge is the local web workbench for ACP. Its purpose is to turn the current
command-line and stage-based workflow system into a unified task console for
conformer search, NMR, and mechanism/transition-state workflows.

The core design decision is:

> Use a single-page HTML frontend for user interaction, backed by a local ACP
> API service that owns process execution, queueing, state persistence, logs,
> result discovery, and backend capability checks.

This avoids asking a static HTML file to start Gaussian, xTB, CREST, ORCA, or
Shermo directly. Browser security rules make that approach fragile. The HTML
page should be a clean operator console; the Python backend should be the only
process scheduler.

## 2. Goals

### 2.1 Product Goals

1. Provide one local web entry point for all ACP workflows.
2. Replace manual command construction with guided forms and validated presets.
3. Make long-running jobs visible through queue status, stage progress, live
   logs, result files, and failure summaries.
4. Support the Phase 4 mechanism workflow without creating another isolated
   script layer.
5. Make xTB path generation, Gaussian TS optimization, IRC, endpoint
   refinement, NMR, and conformer search feel like one coherent platform.

### 2.2 Engineering Goals

1. Keep existing CLI workflows usable.
2. Reuse `WorkflowRunner`, `WorkflowState`, backend registry, and current ACP
   workflow functions instead of replacing them.
3. Add a scheduler layer outside `WorkflowRunner`; do not overload stage
   execution with job queue responsibilities.
4. Persist job state so a browser refresh or ACP service restart does not lose
   task history.
5. Keep the first version local-only: bind to `127.0.0.1`, no authentication,
   no remote multi-user deployment.

## 3. Non-Goals

1. xTBridge v1 is not a cloud service.
2. xTBridge v1 will not replace GaussView, Avogadro, or other molecular
   visualization tools.
3. xTBridge v1 will not implement a full visual molecule editor.
4. xTBridge v1 will not solve HPC scheduling beyond local process management
   and future extension hooks.
5. xTBridge v1 will not make xTB path finding equivalent to robust NEB/GSM
   algorithms.

## 4. Important Technical Correction

The implementation must not assume an xTB command of the form:

```bash
xtb multi_frame.xyz --neb ...
```

The xTB documented reaction path interface is the guided path finder:

```bash
xtb start.xyz --path end.xyz --input path.inp
```

Official documentation describes this as a meta-dynamics reaction path finder.
It is simpler and often faster than NEB or GSM, but less reliable for difficult
reaction paths. xTBridge should therefore name this feature `xtb_path` or
`xtb_pathfinder`, not `xtb_neb`.

References:

- xTB Reaction Path Methods: https://xtb-docs.readthedocs.io/en/latest/path.html
- xTB command-line `--path`: https://xtb-docs.readthedocs.io/en/latest/commandline.html

## 5. Current ACP Baseline

> **Updated 2026-06-25 (code re-verification):** Items 5, 6, and the closing
> sentence below were corrected — `acp.api` is no longer a placeholder, `acp run
> serve` is real, and a frontend MVP already exists. See
> `ACP_Frontend_Target_Implementation_Plan.md` §1.5 for the full correction
> matrix.

The repository already has several useful foundations:

1. `src/acp/core/workflow.py`
   - `Stage`
   - `WorkflowSpec`
   - `WorkflowContext`
   - `WorkflowRunner`
   - `WorkflowResult`

2. `src/acp/core/state.py`
   - `WorkflowState`
   - `EventLog`
   - atomic `state.json` persistence

3. `src/acp/backends/`
   - capability protocols
   - backend registry
   - capability matrix
   - Gaussian, ORCA, CREST, xTB wrappers

4. `src/acp/workflows/`
   - conformer workflow
   - NMR workflow
   - benchmark workflow

5. `src/acp/api/` — **IMPLEMENTED (was placeholder)**
   - `server.py` — FastAPI app factory + static frontend hosting at `/`
   - `routes.py` — `/api/status`, `/api/backends` live
   - `schemas.py` — Pydantic models (StatusResponse, BackendsResponse used;
     JobRequest/JobStatus defined but not yet wired to routes)

6. `src/acp/cli.py`
   - `acp run conformer` — real
   - `acp run nmr` — real
   - `acp run mechanism` — placeholder (still returns "not yet implemented")
   - `acp run serve` — **REAL (was placeholder)**: calls
     `uvicorn.run("acp.api.server:app", ...)` with `--host/--port/--reload`

7. `frontend/ACP_Workbench.html` — **EXISTS (dark-theme dashboard)**
   - polls `/api/status`, `/api/backends`
   - has Backends table, Job Queue table, Log panel
   - job submission not wired (depends on scheduler)

The missing layer is now **only the job scheduler** (`src/acp/scheduler/`),
the `/api/jobs` + SSE routes, the `api` optional dependency declaration in
`pyproject.toml`, and schema alignment for `/api/status` & `/api/backends`.

## 6. Target Architecture

```text
ACP_Workbench.html
        |
        | HTTP + Server-Sent Events
        v
127.0.0.1:8765
        |
        +-- acp.api.server
        |     FastAPI app, static frontend, JSON endpoints, SSE streams
        |
        +-- acp.scheduler.manager
        |     job queue, concurrency, cancellation, retries, lifecycle events
        |
        +-- acp.scheduler.runner
        |     maps JobSpec to conformer/NMR/mechanism workflow calls
        |
        +-- acp.scheduler.store
        |     SQLite metadata + per-job state.json + events.jsonl
        |
        +-- acp.scheduler.logs
        |     log tailing, stdout/stderr capture, stage event stream
        |
        +-- acp.workflows.*
              current stage-based scientific workflows
```

## 7. New Package Layout

Create the following modules:

```text
src/acp/api/
├── __init__.py
├── server.py
├── routes.py
├── schemas.py
└── static.py

src/acp/scheduler/
├── __init__.py
├── jobs.py
├── manager.py
├── runner.py
├── store.py
├── events.py
├── logs.py
└── files.py

src/acp/workflows/
└── mechanism.py

frontend/
├── ACP_Workbench.html
├── acp.css
└── acp.js
```

For the first deliverable, `frontend/ACP_Workbench.html` may inline CSS and JS
to reduce packaging friction. Once the API server is stable, split CSS/JS.

## 8. Job Model

### 8.1 JobStatus

Use a scheduler-level status enum that is separate from the lower-level
workflow result status:

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
```

### 8.2 JobSpec

```python
@dataclass(frozen=True)
class JobSpec:
    workflow: str
    name: str
    input: dict[str, object]
    method: dict[str, object]
    resources: dict[str, object]
    output_dir: Path | None = None
    config_path: Path | None = None
    tags: list[str] = field(default_factory=list)
```

Supported `workflow` values:

- `conformer`
- `nmr`
- `mechanism`
- `benchmark`

### 8.3 JobRecord

```python
@dataclass
class JobRecord:
    id: str
    spec: JobSpec
    status: JobStatus
    work_dir: Path
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    current_stage: str | None = None
    progress: float | None = None
    error: str | None = None
    pid: int | None = None
```

### 8.4 Directory Layout Per Job

```text
ACP_runs/
└── 20260625_001_ethanol_conformer/
    ├── job.json
    ├── state.json
    ├── events.jsonl
    ├── stdout.log
    ├── stderr.log
    ├── inputs/
    ├── work/
    ├── results/
    └── report/
```

## 9. Scheduler Design

### 9.1 Responsibilities

`JobManager` owns:

1. accepting jobs
2. assigning job IDs
3. creating work directories
4. validating backend availability
5. enforcing concurrency limits
6. launching workflow execution
7. recording lifecycle events
8. cancellation
9. retry and clone
10. reloading persisted jobs on service start

`WorkflowRunner` remains responsible only for sequential stage execution.

### 9.2 Concurrency

Initial policy:

```text
max_running_jobs = 1
max_gaussian_jobs = 1
max_xtb_jobs = 2
```

This conservative default avoids oversubscribing CPU, memory, and Gaussian
scratch. Later versions can add resource-aware scheduling.

### 9.3 Cancellation

Cancellation is best-effort:

1. Mark job `CANCELLING`.
2. Stop launching new stages.
3. Terminate the subprocess if the workflow is running through a child process.
4. If a stage runs in-process, stage functions must periodically check a
   cancellation token before starting external calculations.
5. Mark final status as `CANCELLED` or `FAILED` depending on outcome.

### 9.4 Execution Strategy

Use in-process execution for the first version:

- easier access to `WorkflowState`
- no command-line parsing overhead
- easier test mocking

Keep a future fallback for subprocess execution:

```text
python -m acp.cli run conformer ...
python -m acp.cli run nmr ...
```

This fallback is useful when isolation is more important than tight integration.

## 10. API Design

### 10.1 Status and Discovery

```text
GET /api/status
GET /api/backends
GET /api/protocols
GET /api/workflows
```

`GET /api/status` response:

```json
{
  "service": "xTBridge",
  "version": "0.1.0",
  "status": "ok",
  "run_root": "E:/Calculations/ACP_runs",
  "queue": {
    "queued": 2,
    "running": 1,
    "completed": 8,
    "failed": 1
  }
}
```

`GET /api/backends` response:

```json
{
  "gaussian": {
    "available": true,
    "path": "g16",
    "capabilities": {
      "geometry_optimization": "available",
      "single_point": "available",
      "frequency": "available",
      "nmr": "available",
      "ts_optimization": "stubbed",
      "irc": "stubbed"
    }
  },
  "xtb": {
    "available": true,
    "path": "xtb",
    "capabilities": {
      "geometry_optimization": "available",
      "single_point": "available",
      "pathfinder": "planned"
    }
  }
}
```

### 10.2 Job Routes

```text
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/cancel
POST /api/jobs/{job_id}/retry
POST /api/jobs/{job_id}/clone
GET  /api/jobs/{job_id}/events
GET  /api/jobs/{job_id}/logs
GET  /api/jobs/{job_id}/files
GET  /api/jobs/{job_id}/report
```

`POST /api/jobs` request:

```json
{
  "workflow": "mechanism",
  "name": "sn2_ts_search",
  "input": {
    "reactant": "reactant.xyz",
    "product": "product.xyz",
    "charge": 0,
    "multiplicity": 1
  },
  "method": {
    "ts_guess": "xtb_path",
    "ts_backend": "gaussian",
    "irc_backend": "gaussian"
  },
  "resources": {
    "nproc": 16,
    "mem": "32GB"
  }
}
```

`GET /api/jobs/{job_id}/events` should use Server-Sent Events:

```text
event: stage
data: {"stage":"xtb_path_guess","status":"running"}

event: log
data: {"stream":"stdout","line":"Running xTB path finder..."}

event: result
data: {"file":"results/reaction_profile.json"}
```

## 11. Frontend Plan

### 11.1 Design Direction

The interface should follow a WeChat article / lightweight WebApp style:

1. narrow readable central column for setup pages
2. clean cards
3. status chips
4. staged vertical workflow
5. collapsible details
6. mobile-friendly layout
7. desktop enhancements for queue and log panes

Use restrained, work-focused styling:

```text
background: #f6f7f9
surface:    #ffffff
text:       #172033
muted:      #687385
primary:    #1677ff
success:    #07c160
warning:    #fa9d3b
danger:     #fa5151
radius:     8px
```

### 11.2 Main Views

1. Dashboard
   - service status
   - backend availability
   - queue summary
   - recent failures

2. New Job
   - workflow selector
   - guided forms
   - preflight validation
   - submit button

3. Queue
   - queued, running, failed, completed filters
   - job cards
   - cancel, retry, clone actions

4. Job Detail
   - stage timeline
   - live logs
   - result files
   - error summary

5. Results
   - searchable completed jobs
   - open result folder
   - download report
   - inspect key files

### 11.3 First Screen Layout

```text
+------------------------------------------------------+
| xTBridge                             service: online |
+------------------------------------------------------+
| Backend Status                                        |
| Gaussian  ok   xTB  ok   CREST  ok   ORCA  missing   |
+------------------------------------------------------+
| New Calculation                                       |
| [Conformer] [NMR] [Mechanism/TS] [Benchmark]          |
+------------------------------------------------------+
| Active Queue                                          |
| running job card                                      |
| queued job card                                       |
+------------------------------------------------------+
| Recent Results                                        |
| completed job card                                    |
+------------------------------------------------------+
```

### 11.4 Form Fields

Conformer form:

- input type: SMILES, XYZ, GJF, LOG, OUT, batch file
- input text or file path
- protocol
- backend preference
- charge
- multiplicity
- nproc
- mem
- output directory

NMR form:

- input source: existing ensemble, conformer job result, standalone file
- backend
- temperature
- energy window
- max conformers
- reference overrides
- output directory

Mechanism form:

- reactant file
- product file
- charge
- multiplicity
- TS guess strategy: `xtb_path`, `gaussian_scan`, `linear_interpolate`
- TS optimization backend
- IRC backend
- nproc
- mem
- output directory

## 12. Mechanism Workflow Plan

### 12.1 Stage List

Create `src/acp/workflows/mechanism.py` with:

```text
stage_prepare_reactant_product
stage_preopt_endpoints
stage_xtb_path_guess
stage_gaussian_ts_opt
stage_verify_ts_frequency
stage_gaussian_irc
stage_endpoint_optimization
stage_single_point_refinement
stage_reaction_profile
stage_write_mechanism_report
```

### 12.2 Stage Responsibilities

`stage_prepare_reactant_product`

- read reactant and product structures
- check same atom count
- check same element ordering
- write normalized endpoint XYZ files
- store metadata in `StructureEnsemble.metadata`

`stage_preopt_endpoints`

- optionally optimize reactant and product with xTB
- preserve original and optimized endpoints
- fail clearly if atom ordering changes

`stage_xtb_path_guess`

- run:

```bash
xtb reactant.xyz --path product.xyz --input path.inp
```

- parse path outputs
- identify TS guess from `xtbpath_ts.xyz` when present
- collect path energies when available
- write `results/ts_guess.xyz`

`stage_gaussian_ts_opt`

- call `GaussianBackend.transition_state_opt`
- use `Opt=(TS,CalcFC,NoEigenTest,MaxCycles=100)` as the first route preset
- store TS log and final coordinates

`stage_verify_ts_frequency`

- run Gaussian frequency
- require exactly one imaginary frequency by default
- record all frequencies
- write a human-readable validation summary

`stage_gaussian_irc`

- run IRC forward and reverse
- parse energies and endpoint coordinates
- write `irc_profile.json`

`stage_endpoint_optimization`

- optimize IRC endpoints
- compare with original reactant/product structures by RMSD

`stage_single_point_refinement`

- optional final SP level from config
- compute final relative energies

`stage_reaction_profile`

- build barrier table
- generate JSON for the frontend chart

`stage_write_mechanism_report`

- write HTML and JSON summary
- include file manifest

### 12.3 Gaussian Interface Changes

Update `src/conformer_search/qc/interfaces/gaussian.py`:

1. Extend `_build_route_line`.
2. Allow `optimize(..., calc_type="ts")`.
3. Add `_write_input(..., addsec=None)`.
4. Add `transition_state_opt`.
5. Add `irc`.
6. Add IRC parsing helper.
7. Add imaginary frequency extraction helper.

Recommended route presets:

```python
calc_type_map = {
    "opt": "Opt",
    "freq": "Freq",
    "sp": "SP",
    "nmr": "NMR=GIAO",
    "optfreq": "Opt Freq",
    "ts": "Opt=(TS,CalcFC,NoEigenTest,MaxCycles=100)",
    "irc_forward": "IRC=(Forward,CalcFC,MaxPoints=50)",
    "irc_reverse": "IRC=(Reverse,CalcFC,MaxPoints=50)",
    "scan": "Opt=ModRedundant",
}
```

Avoid parsing repeated `Standard orientation` blocks with `lines.index(line)`;
use indexed iteration.

### 12.4 xTB Interface Changes

Update `src/conformer_search/qc/interfaces/xtb.py`:

1. Add `pathfinder(...)`, not `neb(...)`.
2. Write a minimal `path.inp`.
3. Run xTB with `--path`.
4. Capture stdout/stderr.
5. Parse `xtbpath_ts.xyz` if present.
6. Return `QCResult` with:
   - `coordinates`
   - `symbols`
   - `metadata["path_files"]`
   - `metadata["ts_guess_file"]`
   - `metadata["path_energies"]`

Update `src/acp/backends/xtb.py` with a wrapper method:

```python
def pathfinder(
    self,
    reactant_xyz: Path,
    product_xyz: Path,
    charge: int = 0,
    multiplicity: int = 1,
    output_dir: Path | None = None,
    **kwargs: Any,
) -> QCResult:
    ...
```

## 13. Capability Matrix Updates

Extend aliases:

```python
"ts": "ts_optimization"
"transition_state": "ts_optimization"
"transition_state_optimization": "ts_optimization"
"irc": "irc"
"pathfinder": "pathfinder"
"xtb_path": "pathfinder"
```

Extend matrix:

```python
"gaussian": {
    "ts_optimization": STUBBED -> AVAILABLE,
    "irc": STUBBED -> AVAILABLE,
    "scan": STUBBED,
}

"xtb": {
    "pathfinder": STUBBED -> AVAILABLE,
}
```

Do not mark a capability `AVAILABLE` until the wrapper method exists and unit
tests cover delegation.

## 14. Persistence Plan

Use a hybrid persistence model:

1. SQLite for job index and queryable fields.
2. `state.json` for workflow state.
3. `events.jsonl` for append-only event stream.
4. plain log files for stdout/stderr and external program logs.

SQLite table:

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    current_stage TEXT,
    progress REAL,
    error TEXT,
    pid INTEGER
);
```

## 15. Logging and Events

Every job should emit:

```json
{"type": "job.created", "job_id": "..."}
{"type": "job.started", "job_id": "..."}
{"type": "stage.started", "stage": "xtb_path_guess"}
{"type": "stage.completed", "stage": "xtb_path_guess"}
{"type": "log", "stream": "stdout", "line": "..."}
{"type": "file.created", "path": "results/ts_guess.xyz"}
{"type": "job.completed", "job_id": "..."}
```

The frontend reads historical events first, then subscribes to SSE for new
events.

## 16. Security Boundaries

For v1:

1. Bind only to `127.0.0.1`.
2. Do not expose arbitrary shell execution.
3. Only allow file operations inside configured roots:
   - project directory
   - run output directory
   - explicitly uploaded input paths
4. Normalize and validate paths before opening or downloading.
5. Do not allow remote URLs as calculation inputs in v1.

## 17. Configuration

Add a top-level config section:

```yaml
xtbridge:
  host: "127.0.0.1"
  port: 8765
  run_root: "./ACP_runs"
  max_running_jobs: 1
  max_gaussian_jobs: 1
  max_xtb_jobs: 2
  open_browser_on_start: true
  log_tail_lines: 300
```

CLI:

```bash
acp run serve --host 127.0.0.1 --port 8765 --run-root ./ACP_runs
```

## 18. Dependencies

Add optional API dependencies:

```toml
[project.optional-dependencies]
api = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "python-multipart>=0.0.9"
]
```

For the MVP, avoid frontend build tooling. Use plain HTML, CSS, and JavaScript.

## 19. Testing Plan

### 19.1 Unit Tests

Add tests for:

1. `JobSpec` validation.
2. `JobStore` create/list/update/reload.
3. `JobManager` queue ordering.
4. `JobManager` cancellation state transitions.
5. API schema serialization.
6. backend capability status.
7. xTB pathfinder command construction.
8. Gaussian TS route construction.
9. IRC parser with fixture logs.
10. frontend static asset serving.

### 19.2 Integration Tests

Use mocked external binaries for default CI-like tests:

1. fake `xtb` writes `xtbpath_ts.xyz`.
2. fake `g16` writes minimal Gaussian logs.
3. scheduler runs a mechanism job through all mocked stages.
4. SSE emits stage and log events.

Mark real external tests:

```python
@pytest.mark.requires_xtb
@pytest.mark.requires_gaussian
```

### 19.3 Manual Tests

1. Start server with `acp run serve`.
2. Open browser at `http://127.0.0.1:8765`.
3. Submit a conformer job from SMILES.
4. Submit an NMR job from a previous conformer result.
5. Submit a mechanism job with two XYZ endpoints.
6. Refresh browser during a running job.
7. Cancel a running job.
8. Retry a failed job.
9. Confirm result files are discoverable.

## 20. Milestones

### M0: Plan and Scaffolding

Deliverables:

- this implementation plan
- `docs/` directory
- selected names: xTBridge, pathfinder, JobManager

Acceptance:

- plan reviewed
- no implementation ambiguity around xTB `--path`

### M1: Local API Server Skeleton

Files:

- `src/acp/api/server.py`
- `src/acp/api/routes.py`
- `src/acp/api/schemas.py`
- `src/acp/cli.py`

Deliverables:

- `acp run serve`
- `GET /api/status`
- static frontend serving

Acceptance:

- `acp run serve --port 8765` starts a local server
- browser opens xTBridge landing page
- tests cover parser and status route

Estimated effort: 1 day

### M2: Scheduler Core

Files:

- `src/acp/scheduler/jobs.py`
- `src/acp/scheduler/store.py`
- `src/acp/scheduler/manager.py`
- `src/acp/scheduler/events.py`

Deliverables:

- create/list/get jobs
- SQLite persistence
- event log
- basic queue state transitions

Acceptance:

- jobs survive service restart
- queued jobs transition to running/completed with a fake runner

Estimated effort: 2 days

### M3: Frontend MVP

Files:

- `frontend/ACP_Workbench.html`
- `frontend/acp.css`
- `frontend/acp.js`

Deliverables:

- dashboard
- backend status cards
- new job form
- queue list
- job detail view
- log panel

Acceptance:

- user can create a fake job
- queue updates without page reload
- mobile and desktop layouts remain usable

Estimated effort: 2 days

### M4: Conformer Workflow Integration

Files:

- `src/acp/scheduler/runner.py`
- `src/acp/workflows/conformer.py`
- API route tests

Deliverables:

- submit conformer job through HTML
- run existing `run_conformer_search`
- display stage status and final files

Acceptance:

- one SMILES conformer job completes through xTBridge
- final ensemble path appears in result panel

Estimated effort: 1-2 days

### M5: NMR Workflow Integration

Files:

- `src/acp/scheduler/runner.py`
- `src/acp/workflows/nmr.py`
- frontend NMR form

Deliverables:

- submit NMR job
- select existing conformer output
- show JSON/XLSX report links

Acceptance:

- mocked NMR backend completes through scheduler
- real Gaussian NMR remains behind existing markers

Estimated effort: 1-2 days

### M6: xTB Pathfinder Backend

Files:

- `src/conformer_search/qc/interfaces/xtb.py`
- `src/acp/backends/xtb.py`
- `src/acp/backends/base.py`
- `src/acp/backends/capabilities.py`

Deliverables:

- `XTBInterface.pathfinder`
- `XTBBackend.pathfinder`
- capability matrix support
- fake xTB fixture tests

Acceptance:

- command uses `--path`, not `--neb`
- parser reads `xtbpath_ts.xyz`
- frontend can show TS guess file

Estimated effort: 1-2 days

### M7: Gaussian TS and IRC Backend

Files:

- `src/conformer_search/qc/interfaces/gaussian.py`
- `src/acp/backends/gaussian.py`
- `src/acp/backends/base.py`
- `src/acp/backends/capabilities.py`

Deliverables:

- `transition_state_opt`
- `irc`
- imaginary frequency metadata
- IRC path metadata
- parser tests

Acceptance:

- TS route is generated correctly
- one imaginary frequency check is exposed in `QCResult.metadata`
- IRC parser handles repeated orientation blocks

Estimated effort: 2-3 days

### M8: Mechanism Workflow

Files:

- `src/acp/workflows/mechanism.py`
- `src/acp/cli.py`
- frontend mechanism form

Deliverables:

- endpoint preparation
- xTB TS guess
- Gaussian TS opt
- frequency verification
- IRC
- reaction profile JSON
- mechanism report

Acceptance:

- mocked mechanism workflow runs end to end
- `acp run mechanism` is no longer a placeholder
- tests updated accordingly

Estimated effort: 3-4 days

### M9: Result Center and Reports

Files:

- `src/acp/scheduler/files.py`
- `src/acp/reports/job_report.py`
- frontend results view

Deliverables:

- file manifest
- report links
- ZIP export
- failure summary

Acceptance:

- completed jobs are searchable
- result files can be opened or downloaded through the local API

Estimated effort: 1-2 days

## 21. Estimated Total Effort

Conservative estimate:

```text
M1-M3: 5 days
M4-M5: 2-4 days
M6-M8: 6-9 days
M9:    1-2 days
Total: 14-20 person-days
```

The largest uncertainty is not the frontend. The largest uncertainty is robust
scientific workflow behavior for xTB path finding, Gaussian TS optimization,
frequency validation, and IRC parsing.

## 22. Acceptance Criteria for xTBridge v1

xTBridge v1 is complete when:

1. `acp run serve` starts the local web console.
2. The console can create conformer, NMR, and mechanism jobs.
3. The scheduler persists jobs and events.
4. The queue can show running, completed, failed, and cancelled jobs.
5. Logs stream live in the browser.
6. Result files are discoverable from the job detail page.
7. xTB pathfinder uses documented `--path` behavior.
8. Gaussian TS and IRC capabilities are exposed through ACP backends.
9. `acp run mechanism` has real behavior or clearly delegates to the same
   scheduler/workflow path.
10. Unit and mocked integration tests pass without requiring commercial
    binaries.

## 23. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| xTB path finder fails on difficult reactions | TS guess unavailable | Add Gaussian scan and interpolation fallback |
| Gaussian TS optimization fails or converges to wrong point | wrong mechanism result | Require frequency verification and explicit warning |
| IRC parsing is brittle | bad endpoints/profile | Use fixture logs and indexed parsing |
| Browser refresh loses visible state | poor UX | Persist jobs and replay events |
| User oversubscribes CPU/memory | failed external jobs | conservative concurrency defaults |
| Static HTML tries to do too much | fragile architecture | keep execution in local API service |
| API dependencies complicate installation | friction | put FastAPI/uvicorn under optional `api` extra |

## 24. Recommended First PR Scope

The first implementation PR should be intentionally small:

1. Add optional API dependencies.
2. Implement `acp run serve`.
3. Serve a static `ACP_Workbench.html`.
4. Add `GET /api/status`.
5. Add `GET /api/backends`.
6. Add tests for CLI parser and status route.

Do not implement mechanism chemistry in the first PR. The first PR should prove
that the local web console and service boundary are correct.

## 25. Recommended Second PR Scope

1. Add scheduler package.
2. Add SQLite job store.
3. Add create/list/get job endpoints.
4. Add fake runner.
5. Connect frontend queue cards to the API.

This validates the real scheduling architecture before expensive scientific
workflows are attached.

## 26. Recommended Third PR Scope

1. Attach conformer workflow.
2. Stream logs and stage events.
3. Display result files.
4. Add retry and cancel.

After this PR, xTBridge becomes useful for existing ACP workflows.

## 27. Open Questions

1. Should xTBridge store all run output under a global `ACP_runs/` directory, or
   under user-selected output directories?
2. Should browser "open folder" be implemented through a local API helper, or
   should the UI only display paths?
3. Should mechanism jobs require preoptimized endpoints, or should endpoint
   preoptimization be automatic by default?
4. Should Gaussian TS route presets live in YAML config or Python constants?
5. Should xTBridge eventually support remote HPC adapters, or stay local-only
   for v1?

## 28. Final Recommendation

Build xTBridge in this order:

```text
local API shell -> scheduler -> frontend queue -> conformer integration
-> NMR integration -> xTB pathfinder -> Gaussian TS/IRC -> mechanism workflow
-> result center
```

This order gives ACP a usable frontend early while keeping the high-risk
mechanism chemistry work isolated until the scheduling foundation is stable.
