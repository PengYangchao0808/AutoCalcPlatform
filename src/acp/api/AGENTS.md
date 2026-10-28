# acp/api/ — FastAPI Web API (Phase 2 + ACP 2.0 science/research read surfaces)

## OVERVIEW
FastAPI application providing REST endpoints for job submission, backend/workflow discovery, SSE event streaming, and static frontend hosting. Wires into `acp.scheduler` for job lifecycle; data reads and commands go through `acp_services` (Data prepare/read, Runtime commands, research runtime). 20 `.py`, ~19.2k lines (2026-10-26). Services read surfaces (`GET /api/v1/jobs/{id}/availability`, `GET /api/v1/jobs/{id}/prepared/{resource}`) coexist with the legacy GET paths; control endpoints go through `acp_services.runtime.commands.RuntimeCommands`. Legacy GET behavior is retained for F0 compatibility (closure L2-02/L2-03). ACP 2.0 adds read-only science/research/approval/budget routers; the science GETs are side-effect-free (`manager.store.counts()` unchanged, test-pinned).

## STRUCTURE
```
api/
├── __init__.py                    # 5 L — __all__ = ["server", "routes", "schemas"] (module names, not symbols)
├── server.py                      # 476 L — create_app() factory (SCHEDULER-LESS; JobManager/admission/research store on lifespan), deployment tier + OriginPolicy/identity middleware, mounts 11 routers + legacy /api, serves frontend at / and /legacy/
├── routes.py                      # 1107 L / 16 routes — /api status/health/capabilities/identity, backends/workflows/protocols, job create/list/get/cancel/logs/files, SSE (/events/stream)
├── v1_routes.py                   # 8970 L / 104 routes — job submission, uploads, tasks, detail, recovery matrix, edit-recalculate, PES review/energy graph/IRC, availability/prepared/prepare/preflight/operations/analyze/events
├── v1_science.py                  # 171 L / 1 route — GET /api/v1/science/tasks/{job_id}/attempts/{attempt}/status (own gate chain; read-root resolver; 4 wire fields)
├── v1_schemas.py                  # 2075 L — V1 Pydantic models (incl. V1PreflightRequest, JobLiveStatus/JobLiveMetric)
├── drafts.py                      # 168 L / 5 routes — /drafts CRUD for Workbench create-task drafts (create_task_drafts table)
├── batch_preview.py               # 217 L / 1 route — POST /batch-optimize/config-preview (pure read-only projection)
├── job_status_view.py             # 279 L — pure live-status/display-method projections consumed by v1_routes
├── mechanism_readonly.py          # 120 L / 3 routes + mechanism_readonly_schemas.py 59 L — historical mechanism-project display only
├── v2_routes.py                   # 1861 L / 25 routes — task views, batch ops, molecule groups, lineage
├── v2_schemas.py                  # 410 L — V2 Pydantic models
├── v2_structure_sources.py        # 591 L / 12 routes — structure-source facets/organization/tags/trash
├── v2_science.py                  # 510 L / 4 routes — science capabilities, zero-side-effect preflight, attempt view, artifact read
├── v2_research.py                 # 227 L / 2 routes — read-only proposal/research-record projections
├── v2_approvals.py                # 445 L / 6 routes — research approval request/query/read + approve/reject/revoke
├── v2_budgets.py                  # 139 L / 3 routes — budget read, evidence export, receipt settlement
├── science_projection.py          # 1204 L — pure projection primitives (attempt_status, preflight reshape, artifact-id safety, prepare-state vocabulary); imports the MCP TOOL_ALLOWLIST
└── schemas.py                     # 187 L — shared status/backends/jobs Pydantic models
```

## WHERE TO LOOK
| File | Key Contents |
|------|------------|
| `server.py` | `create_app()` factory — scheduler-less: lifespan builds `JobManager` + local admission + `ServerIdentity`/`MultiUserIdentity` + `ResearchResourceStore` (`<run_root>/acp_research.db`); deployment tier/capability gate + `OriginPolicy`/identity middleware; mounts the 11 routers at L430–441 (v2_science at L438) plus the legacy `/api` router; serves `/` + `/legacy/` HTML and `/js` + `/css` via `_RevalidatingStaticFiles` (`Cache-Control: no-cache`) so a normal reload always picks up newly deployed workbench JS/CSS |
| `routes.py` | `/api` base router (16 routes): `/status`/`/health`/`/capabilities`/`/identity`, `/backends`, `/workflows`, `/protocols`, `/jobs` create/list/get/cancel/logs/files, SSE `/events/stream`. Workflow metadata is registry-derived (`list_workflow_entries`), not a static list |
| `v1_routes.py` | 104 routes: job submission, molecule upload/parse, task/recovery matrix, edit-recalculate, PES review/energy graph/IRC; control endpoints build `RuntimeCommands` (`acp_services.runtime.commands`) via `build_runtime_commands`; read-only `GET /jobs/{job_id}/availability` and `GET /jobs/{job_id}/prepared/{resource}` use Data `resource_read`; structure-viewer remote files go through the manager-owned `RemoteStructureCache` singleton (`JobManager.structure_cache`) — never build a second cache instance; PES/energy reads resolve remote jobs via `_job_read_root` (cache-first, terminal catalog fetch); remote PES review writes stage in the cache root and write back over SFTP (`_remote_review_setup` → `save_pes_review` → `_remote_review_write_back`). `POST /jobs/{id}/prepare`, `POST /preflight`, `GET /operations/{operation_id}` expose the prepare-operation service; `GET /jobs/{id}/events?after_seq=` is a cursor stream delegating to `legacy_stream_job_events`; `POST /jobs/{id}/analyze` is registry-gated (`acp_services.runtime.analysis_capabilities.ANALYSIS_CAPABILITY_IDS` + `DEFAULT_ANALYSIS_REGISTRY`). Legacy GET behavior (including `_job_read_root` terminal `fetch_catalog`) is deliberately retained for F0 (closure L2-03) |
| `v1_science.py` | `GET /api/v1/science/tasks/{job_id}/attempts/{attempt}/status` — deliberately **not** on the shared `v1_router`; owns its gate chain (attempt ≥ 1 → job scope 404 → verified project → controlled read root → projection), resolves via `build_read_root_resolver` and **never** `_job_read_root`; wire payload is exactly `schema_version`/`task_ref`/`status`/`revision` (no `work_dir`/`pid`/`host`/`remote_job_id`/`release`) |
| `drafts.py` | Workbench persistent create-task drafts: `/drafts` GET/POST/GET{id}/PUT/DELETE; table `create_task_drafts` in the jobs DB; body capped at 8 MB; `revision` bumped per save |
| `batch_preview.py` | `POST /api/v1/batch-optimize/config-preview` — normalized per-role effective options + source provenance + ORCA keyword summaries; **no job creation, no disk writes**; accepts legacy flat payloads and `batch_roles` |
| `job_status_view.py` | `build_live_status()` / `resolve_display_method()` — pure functions over `state.json` mappings; stage labels, metrics sanitation, PES/batch/IRC/simple method display. No I/O |
| `mechanism_readonly.py` | Historical mechanism-project list/create/detail (s1–s4 artifacts) for display only; extracted from v1 to isolate MechanismProject/Lowconfirm/Highconfirm terms (gate `final_stage_terms`) |
| `v2_routes.py` | 25 routes: project task trees/views/facets, orphans, batch ops, tags, molecule groups/merge, lineage |
| `v2_structure_sources.py` | 12 routes: `structure-sources` facets/list/detail, candidate status/assessments, metadata patch, project structure tags, trash purge |
| `v2_science.py` | 4 routes under `/api/v2`: `GET /science/capabilities`, `POST /science/preflight` (zero-side-effect, same `_preflight_projection` + plan digest), `GET /science/tasks/{id}/attempts/{a}/view`, `GET .../artifacts/{artifact_id}/read`. Read-only; one read root via `build_read_root_resolver`. ⚠ Its module docstring still says "not mounted by `server.py`" — **stale**: `server.py` L438 mounts it |
| `v2_research.py` | Read-only `GET /research/proposals/{proposal_id}` + `GET /research/records/{research_id}` → `acp_services.data.research_resources.ResearchResourceStore`; returns the stored document verbatim; never writes/revokes |
| `v2_approvals.py` | Persistent proposal approvals via `acp_services.runtime.approvals.ApprovalService`: `POST/GET /research/approvals`, `GET /research/approvals/{ref}`, `POST .../{ref}/approve` / `reject` / `revoke`; principal comes from the authenticated Controller channel, never the payload; approval ≠ task state |
| `v2_budgets.py` | `GET /research/budgets/{id}`, `GET .../evidence`, `POST .../settle` → `acp_services.runtime.admission.ReservationLedger` (`research_budget_projection`/`research_budget_evidence`/`settle_research_budget`) with value objects from `acp_services.runtime.research_budget`; receipt id is deterministic (duplicate delivery is a no-op); budget scope derives from the B16 approval |
| `science_projection.py` | Pure read-only projections: `attempt_status`, `attempt_view`, `artifact_read`, `science_capabilities`, `science_preflight`, `map_availability`, artifact-id safety (`SAFE_ARTIFACT_ID_RE`/`is_safe_artifact_id`), prepare-state vocabulary. Imports only metadata/read contracts (`acp.catalog`, `acp.storage.manifest`, `cccp.calculation.{capabilities,requests}`, `acp_services.data.operations`, `acp_services.entrypoints.{mcp,status_projection}`) — adding a DB or server import is a bug |
| `schemas.py` / `v1_schemas.py` / `v2_schemas.py` | Pydantic contracts: shared status/backends/jobs (`schemas.py`), V1 job/detail/preflight/live-status models (`v1_schemas.py`), V2 task-view/group/lineage models (`v2_schemas.py`) |
| MCP relationship | `acp mcp serve` is a thin stdio adapter over `acp_services.entrypoints.mcp_stdio.run_stdio` + `acp/services_mcp_bootstrap.build_mcp_runtime`; protocol/allowlist owner is `acp_services/entrypoints/mcp.py` (`TOOL_ALLOWLIST`, 8 tools: `acp_capabilities`, `acp_preflight`, `acp_submit`, `acp_status`, `acp_cancel`, `acp_prepare_artifact`, `acp_read_artifact`, `acp_analyze`), each 1:1 with a Controller HTTP command. `science_projection.py` imports the same allowlist — there is no second list |
| Tests | `tests/test_acp2_v1_science_status.py` and `tests/test_acp2_v2_science_routes.py` pin zero side effects via `manager.store.counts()`; `tests/test_acp2_research_read_routes.py`, `tests/test_acp2_api_science_routes.py`, `tests/test_acp2_science_projection.py` cover the read surfaces |

## CONVENTIONS
- Same as parent `acp/`: PEP 604 annotations (`X | None`), `from __future__ import annotations`, compact docstrings
- `APIRouter`-based routing — `/api/v1`: `v1_router`, `drafts_router`, `mechanism_readonly_router`, `batch_preview_router`, `v1_science.router`; `/api/v2`: `v2_router`, `v2_approvals_router`, `v2_budgets_router`, `v2_struct_router`, `v2_science.router`, `v2_research.router`; `/api`: `routes.router`
- Scheduler dependency injected via `request.app.state.job_manager` (lifespan-managed); admission/research store/identity live on the same `app.state`
- **Read-only surfaces stay side-effect-free**: science/research GETs never create a task, prepare/transfer a resource, take a reservation or trigger a GET-time catalog fetch. Use `build_read_root_resolver` for attempt reads, never `_job_read_root`. Guard tests assert `manager.store.counts()` is unchanged
- **Gate order**: required project scope → credential-scoped project → verified server identity → entry access (`_require_entry_access`) → server-side project claim → resource lookup. Unknown and cross-project ids share the same 404 (existence never leaks)
- Frontend HTML read from `frontend/ACP_Workbench_v2.html` (preferred) or fallback `ACP_Workbench.html`; when the checkout `frontend/` is absent the packaged `acp_web_assets` module directory is used (`find_spec`)
- `__all__` in `__init__.py` lists module names (`["server", "routes", "schemas"]`) — NOT module symbols
- `[api]` extra owns `fastapi`/`uvicorn`/`python-multipart`/`zipstream-ng` — import guards are not a substitute for the declared extra

## ANTI-PATTERNS
- **Module-level side effect**: `server.py` L474 `app = create_app()` runs at import — it resolves run_root, creates directories and loads remote/retention config (the scheduler itself is built on lifespan, so re-init under uvicorn `--reload` is bounded, not absent). Keep `ACP_RUN_ROOT`/`ACP_HOST`/`ACP_PORT`/`ACP_MAX_RUNNING`/`ACP_POLL_INTERVAL` authoritative; a second concurrent startup for the same run_root is refused by the manager's ownership guard
- **GET with write/network side effects**: a read route must not call `_job_read_root` (terminal `fetch_catalog`), enqueue a prepare operation, or mutate `store`. `GET /jobs/{id}/availability` and the science routers are the contract; the legacy GET fallback is the only intentional exception (closure L2-03)
- **Bare `except Exception:`**: 50 instances across `api/*.py` (38 in `v1_routes.py`, 6 in `v2_routes.py`, 4 in `server.py`, 1 each in `routes.py`/`job_status_view.py`) silently swallow errors — notably `_load_executables()` and protocol listing. Do not add new ones (root ANTI #8)
- **`__all__` lists module names, not symbols**: `__init__.py` re-exports `["server", "routes", "schemas"]` instead of actual symbols like `app`, `create_app`, `router` — `from acp.api import app` does not work
- **Pyright suppressions are present**: `v1_routes.py` carries a ~20-rule header and `job_status_view.py` 8 rules (pyright is not in the toolchain). Do not treat the package as suppression-free
- **Hardcoded workflow metadata**: `routes.py::_build_workflow_info()` must stay registry-derived (`acp.workflows.registry.list_workflow_entries` / `PUBLIC_WORKFLOWS`); do not reintroduce a static `_WORKFLOW_INFO` list (old drift bug)
- **Deleting legacy GET before F0 gate**: `/availability` and `/prepared/{resource}` are additive; old GET paths stay until the F0/F1×S0/S1 matrix and zero-consumer evidence close (closure L2-03/§7.4)
- **Trusting the stale `v2_science.py` docstring**: it claims the router is "not mounted by `server.py`" — `server.py` L438 mounts it. Server wiring is the authority; fix the docstring when touching that module
