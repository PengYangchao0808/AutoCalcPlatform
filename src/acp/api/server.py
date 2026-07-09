"""
API Server
==========

FastAPI application factory. Wires the API router, static frontend hosting,
and a process-wide :class:`~acp.scheduler.manager.JobManager` via lifespan.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from acp.api.routes import router as api_router
from acp.api.v1_routes import router as v1_router

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"


def create_app(
    run_root: Path | str | None = None,
    host: str | None = None,
    port: int | None = None,
    max_running: int | None = None,
) -> FastAPI:
    """Build the FastAPI app with a scheduler bound to ``run_root``.

    Reads ``ACP_RUN_ROOT`` / ``ACP_HOST`` / ``ACP_PORT`` / ``ACP_MAX_RUNNING``
    env vars (set by ``acp run serve``) so the configuration survives uvicorn's
    module re-import under ``--reload``.
    """
    from acp.scheduler.manager import JobManager

    run_root_path = Path(run_root or os.environ.get("ACP_RUN_ROOT", "./ACP_runs")).resolve()
    eff_host = host or os.environ.get("ACP_HOST", "127.0.0.1")
    eff_port = int(port if port is not None else os.environ.get("ACP_PORT", "8765"))
    max_running_env = os.environ.get("ACP_MAX_RUNNING", "1")
    eff_max = int(max_running if max_running is not None else max_running_env)
    run_root_path.mkdir(parents=True, exist_ok=True)
    manager = JobManager(run_root=run_root_path, max_running=eff_max)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.job_manager = manager
        app.state.db_path = str(manager.store.db_path)
        app.state.host = eff_host
        app.state.port = eff_port
        app.state.run_root = str(run_root_path)
        try:
            yield
        finally:
            manager.shutdown()

    app = FastAPI(
        title="ACP — Auto-Calc Platform",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(api_router, prefix="/api")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        html_path = _FRONTEND_DIR / "ACP_Workbench_v2.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
        return HTMLResponse(
            content="<h1>ACP Server Running</h1><p>frontend/ACP_Workbench_v2.html not found</p>"
        )

    @app.get("/legacy/", response_class=HTMLResponse, include_in_schema=False)
    def legacy_index() -> HTMLResponse:
        html_path = _FRONTEND_DIR / "ACP_Workbench.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
        return HTMLResponse(
            content="<h1>ACP Server Running</h1><p>frontend/ACP_Workbench.html not found</p>"
        )

    return app


app = create_app()

__all__ = ["app", "create_app"]
