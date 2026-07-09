# acp/api/ — FastAPI Web API (Phase 2)

## OVERVIEW
FastAPI application providing REST endpoints for job submission, backend discovery, SSE event streaming, and static frontend hosting. 6 files, ~1600 lines. Wires into `acp.scheduler` for job lifecycle.

## STRUCTURE
```
api/
├── __init__.py     # Re-exports: ["server", "routes", "schemas"] (4 lines)
├── server.py       # create_app() factory + module-level app = create_app() side effects (92)
├── routes.py       # /api/status, /api/backends, /api/jobs CRUD, SSE, file download (387)
├── v1_routes.py    # Job submission, molecule upload, task management endpoints (700)
├── schemas.py      # Pydantic models: StatusResponse, BackendsResponse, JobCreateRequest+ (170)
└── v1_schemas.py   # V1 API Pydantic models (247)
```

## WHERE TO LOOK
| File | Key Contents |
|------|------------|
| `server.py` | `create_app()` factory — wires routers, lifespan starts `JobManager`, serves frontend at `/` and `/legacy/` |
| `routes.py` | `/api/status` (service health + queue counts), `/api/backends` (capability discovery), `/api/jobs` (create/list/get/cancel/logs/files), `/api/jobs/{id}/events` (SSE stream) |
| `v1_routes.py` | Higher-level job submission, molecule upload, task management (~700 lines) |
| `schemas.py` | Shared Pydantic request/response models for status, backends, jobs, files |
| `v1_schemas.py` | V1-specific Pydantic schemas |

## CONVENTIONS
- Same as parent `acp/`: PEP 604 annotations (`X | None`), `from __future__ import annotations`, compact docstrings
- `APIRouter`-based routing — `routes.py` mounts at `/api`, `v1_routes.py` at `/api/v1`
- Scheduler dependency injected via `request.app.state.job_manager` (lifespan-managed)
- Frontend HTML read from `frontend/ACP_Workbench_v2.html` (preferred) or fallback `ACP_Workbench.html`
- `__all__` in `__init__.py` lists module names (`["server", "routes", "schemas"]`) — NOT module symbols

## ANTI-PATTERNS
- **Module-level side effect**: `server.py` line 90 `app = create_app()` triggers `JobManager` init, directory creation, and config loading at import time. Causes re-init under uvicorn `--reload` (mitigated via env vars `ACP_RUN_ROOT`/`ACP_HOST`/`ACP_PORT`)
- **Bare `except Exception:`**: 6 instances (4 in `v1_routes.py`, 2 in `routes.py`) silently swallow errors — notably `_load_executables()` and protocol listing
- **Missing optional deps**: `fastapi`/`uvicorn` not declared as `[api]` extras in `pyproject.toml` — users must install manually
- **`__all__` lists module names, not symbols**: `__init__.py` re-exports `["server", "routes", "schemas"]` instead of actual symbols like `app`, `create_app`, `router` — breaks `from acp.api import app` in type checkers
- **No pyright suppressions** locally (unlike broader `acp/` package) — but many `# type: ignore` in `v1_routes.py`
- **`_WORKFLOW_INFO` hardcoded**: Workflow metadata in `routes.py` is a static list, not derived from `acp.workflows` registry — drift risk
