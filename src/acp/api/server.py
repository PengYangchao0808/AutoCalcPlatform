"""
API Server
==========

FastAPI application factory. Wires the API router, static frontend hosting,
and a process-wide :class:`~acp.scheduler.manager.JobManager` via lifespan.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from acp import __version__
from acp.api import v1_science, v2_research, v2_science
from acp.api.batch_preview import router as batch_preview_router
from acp.api.drafts import router as drafts_router
from acp.api.mechanism_readonly import router as mechanism_readonly_router
from acp.api.routes import router as api_router
from acp.api.v1_routes import router as v1_router
from acp.api.v2_approvals import router as v2_approvals_router
from acp.api.v2_budgets import router as v2_budgets_router
from acp.api.v2_model_gateway import build_controller_model_gateway
from acp.api.v2_model_gateway import router as v2_model_gateway_router
from acp.api.v2_routes import router as v2_router
from acp.api.v2_structure_sources import router as v2_struct_router
from acp_services.contracts.access import DeploymentTier, is_loopback_host
from acp_services.data.research_resources import ResearchResourceStore
from acp_services.workspace.boundary import (
    API_KEY_HEADER,
    AUTHORIZATION_HEADER,
    LOCAL_CLI_HEADER,
    OriginPolicy,
    require_tier_capabilities,
)
from acp_services.workspace.http_identity import (
    IdentityDenial,
    MultiUserIdentity,
    credential_from_headers,
)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
if not _FRONTEND_DIR.is_dir():
    _assets_spec = find_spec("acp_web_assets")
    if _assets_spec is not None and _assets_spec.origin is not None:
        _FRONTEND_DIR = Path(_assets_spec.origin).parent
logger = logging.getLogger(__name__)


def unknown_field_envelope(exc: RequestValidationError) -> dict[str, Any] | None:
    """Return the frozen ``input_invalid`` envelope for an undeclared field.

    ``None`` when the validation failure is not ``extra_forbidden`` (the caller
    then keeps FastAPI's default error list).  One implementation so every
    ``extra="forbid"`` DTO — not only the submit request — reports the same
    machine-readable shape.
    """
    extra = [error for error in exc.errors() if error.get("type") == "extra_forbidden"]
    if not extra:
        return None
    fields = [str((error.get("loc") or ("",))[-1]) for error in extra]
    return {
        "code": "input_invalid",
        "message": f"request has an undeclared field {fields[0]!r}",
        "retryable": False,
        "params": {"field": fields[0], "rule": "unknown_field", "fields": fields},
    }


class _RevalidatingStaticFiles(StaticFiles):
    """Frontend JS/CSS: force revalidation so deploys reach open browsers.

    A cached bundle must never keep running after the server is upgraded —
    the 2026-09-28 remote pending-fetch incident was sustained by workbench JS
    that predated the terminal retry fix.  ``no-cache`` sends If-None-Match on
    every load; unchanged files answer 304 (cheap), changed files answer 200.
    """

    async def get_response(self, path: str, scope: object) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "no-cache"
        return response


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


def _resolve_deployment_tier() -> str:
    """Deployment tier — a **process/config** fact, never request-supplied.

    Reads ``ACP_DEPLOYMENT_TIER`` (mirroring ``ACP_EXECUTION_MODE``); defaults to
    ``local-single``.  The B01-5 boundary keeps the loopback default and refuses
    to start ``lab`` / ``hosted`` until auth/isolation capabilities are set.
    """
    raw = os.environ.get("ACP_DEPLOYMENT_TIER", "").strip().lower()
    return raw or DeploymentTier.LOCAL_SINGLE


def _resolve_tier_capabilities() -> tuple[str, ...]:
    """Configured tier capabilities (``ACP_TIER_CAPABILITIES``, comma list)."""
    raw = os.environ.get("ACP_TIER_CAPABILITIES", "")
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _resolve_public_origin() -> str | None:
    """Read the init config, allowing an explicit environment override/reset."""
    if "ACP_PUBLIC_ORIGIN" in os.environ:
        return os.environ["ACP_PUBLIC_ORIGIN"].strip() or None

    from cccp.config import load_config

    deployment = load_config().get("deployment") or {}
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be a mapping")
    params = deployment.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("deployment.params must be a mapping")
    origin = params.get("public_origin", "")
    if not isinstance(origin, str):
        raise ValueError("deployment.params.public_origin must be a string")
    return origin.strip() or None


def _is_event_stream_request(request: Request) -> bool:
    """Whether *request* is an SSE connection (diagnostic reason only)."""
    if "text/event-stream" in request.headers.get("accept", ""):
        return True
    return request.url.path.endswith("/events")


_LOCAL_DENIAL = IdentityDenial("no_grant")
_IDENTITY_EXEMPT_PATHS = frozenset(
    {
        "/",
        "/index.html",
        "/api/health",
        "/api/status",
        "/api/capabilities",
        "/api/backends",
        "/api/workflows",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/favicon.ico",
    }
)
_IDENTITY_EXEMPT_PREFIXES = ("/docs/", "/redoc/", "/assets/", "/static/")


#: The Workbench page's own static asset mounts.  A browser session with an
#: API-key identity must be able to load the page's scripts/styles or the
#: product UI cannot run at all (found by the M4-02 browser leg: `/js/...`
#: answered 401 under ``ACP_IDENTITY_MODE=api-key``).  These mounts serve
#: committed frontend assets only — never a task, an approval, a budget or any
#: other tenant-scoped read — so they carry no authorization fact.
_STATIC_ASSET_PREFIXES = ("/js/", "/css/", "/legacy/")


def _identity_exempt(path: str) -> bool:
    """Discovery/static routes that must stay reachable without a credential."""
    if path in _IDENTITY_EXEMPT_PATHS:
        return True
    return any(
        path.startswith(prefix) for prefix in (*_IDENTITY_EXEMPT_PREFIXES, *_STATIC_ASSET_PREFIXES)
    )


def _maybe_configure_structured_logging() -> bool:
    """Opt the API process into structured JSON logging when configured.

    Additive: returns ``False`` (and touches no logger) unless
    ``ACP_LOG_FORMAT=json`` is set (B08-6 / todo 47).  Observation only — the
    logging pipeline never becomes a settlement/recovery authority.
    """
    from acp_services.entrypoints.diagnostics import (
        configure_structured_logging,
        structured_logging_enabled,
    )

    if not structured_logging_enabled():
        return False
    configure_structured_logging()
    return True


def create_app(
    run_root: Path | str | None = None,
    host: str | None = None,
    port: int | None = None,
    max_running: int | None = None,
    poll_interval: int | None = None,
) -> FastAPI:
    """Build the FastAPI app with a lifespan-owned scheduler bound to ``run_root``.

    The returned app is SCHEDULER-LESS: no ``JobManager`` is constructed, no
    threads start, and no run_root ownership is claimed.  The manager is built
    on lifespan startup (so a second concurrent startup for the same resolved
    run_root is refused) and shut down on lifespan exit (stopping all background
    work before releasing ownership).

    Reads ``ACP_RUN_ROOT`` / ``ACP_HOST`` / ``ACP_PORT`` / ``ACP_MAX_RUNNING`` /
    ``ACP_POLL_INTERVAL`` env vars (set by ``acp run serve``) so the
    configuration survives uvicorn's module re-import under ``--reload``.
    """
    from acp.core.paths import check_run_root_safety, resolve_run_root

    _maybe_configure_structured_logging()

    run_root_path = resolve_run_root(run_root or os.environ.get("ACP_RUN_ROOT"))
    eff_host = host or os.environ.get("ACP_HOST", "127.0.0.1")
    eff_port = int(port if port is not None else os.environ.get("ACP_PORT", "8765"))
    max_running_env = os.environ.get("ACP_MAX_RUNNING", "1")
    eff_max = int(max_running if max_running is not None else max_running_env)
    poll_interval_env = os.environ.get("ACP_POLL_INTERVAL", "5")
    eff_poll = int(poll_interval if poll_interval is not None else poll_interval_env)
    run_root_path.mkdir(parents=True, exist_ok=True)
    for safety_message in check_run_root_safety(run_root_path):
        import logging

        logging.getLogger(__name__).warning("run_root safety: %s", safety_message)

    remote_config = _load_remote_config()
    _apply_execution_mode_override(remote_config)
    local_retention = _load_local_retention_config()
    local_interval = _local_cleanup_interval_hours()
    local_max_jobs = _load_local_max_jobs()

    # B01-5: the deployment tier is process/config-only.  Refuse to *enable*
    # lab/hosted while authentication/isolation capabilities are absent; a
    # request can never downgrade the tier (there is no request input here).
    configured_tier = _resolve_deployment_tier()
    require_tier_capabilities(configured_tier, _resolve_tier_capabilities())
    boundary_policy = OriginPolicy(
        tier=configured_tier,
        server_is_loopback=is_loopback_host(eff_host),
        public_origin=_resolve_public_origin(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from acp.scheduler.manager import JobManager
        from acp.services_analysis_bootstrap import build_analysis_registry
        from acp.services_science_plugins_bootstrap import (
            build_plugin_analysis_capabilities,
            build_science_plugin_registry,
        )
        from acp_services.runtime.local_admission import build_local_admission
        from acp_services.workspace.server_identity import (
            ServerIdentity,
            resolve_server_project,
        )

        server_identity = ServerIdentity.build(verified_project=resolve_server_project())
        identity_boundary = MultiUserIdentity.from_environment(run_root_path)
        admission = build_local_admission(
            run_root_path / "acp_admission.db",
            verified_project=server_identity.verified_project,
        )
        manager = JobManager(
            run_root=run_root_path,
            max_running=eff_max,
            poll_interval=eff_poll,
            remote_config=remote_config,
            local_retention_config=local_retention,
            local_cleanup_interval_hours=local_interval,
            local_max_jobs=local_max_jobs,
            idempotency_reservations=admission.reservation_port(),
            admission=admission,
        )
        try:
            app.state.job_manager = manager
            app.state.server_identity = server_identity
            app.state.identity_boundary = identity_boundary
            app.state.identity_mode = identity_boundary.mode
            app.state.admission = admission
            app.state.db_path = str(manager.store.db_path)
            app.state.host = eff_host
            app.state.port = eff_port
            app.state.run_root = str(run_root_path)
            # M4-02: the one Controller model gateway (M3-03) bound to the
            # admission ledger.  DSH/RPH reach a provider only through this
            # object; the credential stays in the process environment.
            app.state.model_gateway = build_controller_model_gateway(
                admission.ledger, run_root_path
            )
            # Dedicated DB file: never the jobs DB.
            app.state.research_resources = ResearchResourceStore(run_root_path / "acp_research.db")
            # M2-02: the application-level analysis registry is built once here
            # (never the module-level default) and is the only table the analyze
            # command and the discovery projection read.  `vibration.analyze` is
            # executable over prepared artifacts; `vibration.compute` stays a
            # declared compute capability (a frequency task, not an analysis).
            # M2-05: enabled third-party pure-analysis plugins are registered
            # into the *same* injected registry from the install/enablement
            # facts, so a new process serves them without any manual register.
            plugin_registry = build_science_plugin_registry(run_root=run_root_path)
            # Fail closed before serving: a plugin that violates the contract
            # (licence, version range, conflicting name/capability, unreadable
            # payload) refuses startup instead of becoming silently discoverable.
            plugin_registry.scan()
            app.state.science_plugin_registry = plugin_registry
            prepare_operations = getattr(manager, "_prepare_operations", None)
            plugin_analysis = build_plugin_analysis_capabilities(
                registry=plugin_registry,
                prepare_operations=prepare_operations,
                artifact_store_root=run_root_path / "artifact-store",
            )
            app.state.analysis_registry = build_analysis_registry(
                prepare_operations=prepare_operations,
                artifact_store_root=run_root_path / "artifact-store",
                plugin_capabilities=plugin_analysis,
            )
            yield
        finally:
            # A store-close failure must never skip the manager shutdown.
            research_store = getattr(app.state, "research_resources", None)
            try:
                if research_store is not None:
                    research_store.close()
            finally:
                manager.shutdown()

    app = FastAPI(
        title="ACP — Auto-Calc Platform",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _api_key_identity(request: Request, call_next):
        """Resolve the opt-in API-key principal before any task entry runs.

        The default ``local-single`` mode never enters this branch.  A denied
        credential returns one uniform 401 so revoked/expired/unknown keys and
        missing keys are indistinguishable to a client.
        """
        boundary = getattr(app.state, "identity_boundary", None)
        if boundary is None or not boundary.enabled:
            return await call_next(request)
        if _identity_exempt(request.url.path):
            return await call_next(request)
        credential = credential_from_headers(dict(request.headers))
        resolved, denial = boundary.resolve(credential)
        if denial is not None or resolved is None:
            return JSONResponse(
                status_code=denial.http_status if denial is not None else 401,
                content={"detail": (denial or _LOCAL_DENIAL).to_dict()},
            )
        request.state.request_identity = resolved
        return await call_next(request)

    @app.middleware("http")
    async def _local_single_boundary(request: Request, call_next):
        """Origin/CSRF guard for state-changing methods (SSE/GET untouched)."""
        headers = request.headers
        decision = boundary_policy.evaluate(
            method=request.method,
            origin=headers.get("origin"),
            host=headers.get("host"),
            is_event_stream=_is_event_stream_request(request),
            has_auth_header=bool(headers.get(AUTHORIZATION_HEADER) or headers.get(API_KEY_HEADER)),
            local_cli_marker=headers.get(LOCAL_CLI_HEADER) is not None,
            client_host=request.client.host if request.client else None,
            server_scheme=request.url.scheme,
        )
        if not decision.allowed:
            return JSONResponse(
                status_code=decision.http_status,
                content={"detail": {"code": decision.reason_code, "message": decision.detail}},
            )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Report an undeclared request field as the shared application error.

        M0-06 freezes ``extra="forbid"`` on the execution DTOs.  FastAPI's default
        422 body is a list of pydantic errors; the frozen application contract is
        ``input_invalid`` with ``params={"field": ..., "rule": "unknown_field"}``
        (``unknown_field_policy`` U1), identical to what the MCP adapter answers
        locally and what the CLI projects.  Every other validation error keeps the
        long-standing FastAPI shape.
        """
        envelope = unknown_field_envelope(exc)
        if envelope is not None:
            return JSONResponse(status_code=422, content={"detail": envelope})
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled API exception",
            extra={"method": request.method, "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"{type(exc).__name__}: {exc}"},
        )

    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(drafts_router, prefix="/api/v1")
    app.include_router(mechanism_readonly_router, prefix="/api/v1")
    app.include_router(batch_preview_router, prefix="/api/v1")
    app.include_router(v2_router, prefix="/api/v2")
    app.include_router(v2_approvals_router, prefix="/api/v2")
    app.include_router(v2_budgets_router, prefix="/api/v2")
    app.include_router(v2_model_gateway_router, prefix="/api/v2")
    app.include_router(v2_struct_router, prefix="/api/v2")
    app.include_router(v2_science.router, prefix="/api/v2")
    app.include_router(v1_science.router, prefix="/api/v1")
    app.include_router(v2_research.router, prefix="/api/v2")
    app.include_router(api_router, prefix="/api")

    # Without these mounts /js|/css 404 and the viewer tabs render blank (46a5626 regression).
    for sub in ("js", "css"):
        asset_dir = _FRONTEND_DIR / sub
        if asset_dir.is_dir():
            app.mount(
                f"/{sub}",
                _RevalidatingStaticFiles(directory=asset_dir),
                name=f"frontend-{sub}",
            )

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
