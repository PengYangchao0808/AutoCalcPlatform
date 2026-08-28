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

from acp import __version__
from acp.api.mechanism_readonly import router as mechanism_readonly_router
from acp.api.routes import router as api_router
from acp.api.v1_routes import router as v1_router
from acp.api.v2_routes import router as v2_router

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"


def _load_remote_config():
    """Load the ``cluster`` section and build a :class:`RemoteExecutionConfig`.

    Called from :func:`create_app`.  Any failure (missing config, bad YAML,
    paramiko not installed) degrades gracefully to a local-only config so
    existing deployments keep working.
    """
    try:
        from acp.scheduler.remote.config import RemoteExecutionConfig
        from cccp.config import load_config

        full_config = load_config()
        cluster_section = full_config.get("cluster", {})
        return RemoteExecutionConfig.from_config_dict(cluster_section)
    except Exception as exc:  # noqa: BLE001 — intentional graceful degradation
        import logging

        logging.getLogger(__name__).warning(
            "Remote execution config load failed, defaulting to local: %s", exc
        )
        from acp.scheduler.remote.config import RemoteExecutionConfig

        return RemoteExecutionConfig(execution_mode="local")


def _load_local_retention_config():
    """Build a :class:`RetentionPolicy` from ``cluster.local_retention`` (Phase 5B).

    Returns ``None`` when local cleanup is disabled (``enabled: false``)
    or the config cannot be loaded — the server then runs without local
    disk protection, matching pre-Phase-5B behaviour.
    """
    try:
        from acp.scheduler.local_cleanup import RetentionPolicy
        from cccp.config import load_config

        full_config = load_config()
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        import logging

        logging.getLogger(__name__).warning(
            "Local retention config load failed, local cleanup disabled: %s", exc
        )
        return None

    local_cfg = full_config.get("cluster", {}).get("local_retention", {})
    if not local_cfg.get("enabled", False):
        return None
    try:
        return RetentionPolicy(
            completed_days=int(local_cfg.get("completed_days", 30)),
            failed_days=int(local_cfg.get("failed_days", 90)),
            cancelled_days=int(local_cfg.get("cancelled_days", 30)),
            db_record_days=int(local_cfg.get("db_record_days", 365)),
            vacuum_after_db_cleanup=bool(local_cfg.get("vacuum_after_db_cleanup", False)),
        )
    except (TypeError, ValueError) as exc:
        import logging

        logging.getLogger(__name__).warning(
            "Invalid local_retention config, local cleanup disabled: %s", exc
        )
        return None


def _local_cleanup_interval_hours() -> int:
    """Read ``cluster.local_retention.cleanup_interval_hours`` (default 6)."""
    try:
        from cccp.config import load_config

        local_cfg = load_config().get("cluster", {}).get("local_retention", {})
        return max(1, int(local_cfg.get("cleanup_interval_hours", 6)))
    except Exception:
        return 6


def _load_local_max_jobs() -> int:
    """Read ``cluster.local.max_jobs`` — the local admission-gate ceiling.

    User-controlled only; never derived from ``os.cpu_count()``.
    """
    try:
        from cccp.config import load_config

        local_cfg = load_config().get("cluster", {}).get("local", {})
        return max(1, int(local_cfg.get("max_jobs", 4)))
    except Exception:
        return 4


def _apply_execution_mode_override(remote_config) -> None:
    """Apply the ``ACP_EXECUTION_MODE`` env override (set by ``acp run serve
    --execution-mode``) on top of the YAML ``cluster.execution_mode``."""
    import logging

    env_mode = os.environ.get("ACP_EXECUTION_MODE", "").strip().lower()
    if not env_mode:
        return
    if env_mode not in ("local", "remote"):
        logging.getLogger(__name__).warning(
            "Invalid ACP_EXECUTION_MODE=%r, ignoring (must be 'local' or 'remote')",
            env_mode,
        )
        return
    remote_config.execution_mode = env_mode


def create_app(
    run_root: Path | str | None = None,
    host: str | None = None,
    port: int | None = None,
    max_running: int | None = None,
    poll_interval: int | None = None,
) -> FastAPI:
    """Build the FastAPI app with a scheduler bound to ``run_root``.

    Reads ``ACP_RUN_ROOT`` / ``ACP_HOST`` / ``ACP_PORT`` / ``ACP_MAX_RUNNING`` /
    ``ACP_POLL_INTERVAL`` env vars (set by ``acp run serve``) so the
    configuration survives uvicorn's module re-import under ``--reload``.
    """
    from acp.scheduler.manager import JobManager

    run_root_path = Path(run_root or os.environ.get("ACP_RUN_ROOT", "./ACP_runs")).resolve()
    eff_host = host or os.environ.get("ACP_HOST", "127.0.0.1")
    eff_port = int(port if port is not None else os.environ.get("ACP_PORT", "8765"))
    max_running_env = os.environ.get("ACP_MAX_RUNNING", "1")
    eff_max = int(max_running if max_running is not None else max_running_env)
    poll_interval_env = os.environ.get("ACP_POLL_INTERVAL", "15")
    eff_poll = int(poll_interval if poll_interval is not None else poll_interval_env)
    run_root_path.mkdir(parents=True, exist_ok=True)

    remote_config = _load_remote_config()
    _apply_execution_mode_override(remote_config)
    local_retention = _load_local_retention_config()
    local_interval = _local_cleanup_interval_hours()

    manager = JobManager(
        run_root=run_root_path,
        max_running=eff_max,
        poll_interval=eff_poll,
        remote_config=remote_config,
        local_retention_config=local_retention,
        local_cleanup_interval_hours=local_interval,
        local_max_jobs=_load_local_max_jobs(),
    )

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
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(mechanism_readonly_router, prefix="/api/v1")
    app.include_router(v2_router, prefix="/api/v2")
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
