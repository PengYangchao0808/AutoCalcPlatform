"""``manual-browser-fixtures`` — model-free Manual Research entry, real browser.

This body is the T-17 / C11 acceptance row.  Two phases, both offline
(``evidence_kind=fixture``):

* **node row** — ``frontend/js/module_tests/run_all.js acp2_manual`` exits 0
  with at least eight passing tests and zero failures (no model config, failed
  preflight disables submit with zero submit calls, charge/multiplicity visible
  and required, retired workflow ids never default, 演示数据 badge, same
  task/attempt detail, wizard payload shape, no fetch);
* **browser row** — ``$ACP2_BROWSER_PYTHON`` drives a real headless Chromium
  (Playwright, frozen browsers root) against the Workbench page served from
  ``frontend/`` on an ephemeral loopback port (never 8765, never the
  production service).  The page is loaded with fixture API routes: capability
  discovery serves fixture metadata (``fixture: true``) and the preflight route
  fails on purpose.  The probe asserts the Manual Research entry is visible,
  the 演示数据 badge is present, and submit is disabled after the failed
  preflight with zero ``POST /jobs`` calls; it captures a full-page screenshot,
  the console-error log and the network log and FAILS on any console error.

A missing prerequisite (Node, source tree, browser interpreter, Chromium) is
``NOT_VERIFIED`` (exit 2); a command that ran and produced a wrong result is
``FAIL`` (exit 1).  Browser, profile, cache, HOME and TMPDIR all live under
``$ACP2_DATA_ROOT``/``$ACP2_TOOLS``; every server/browser process is closed in
``finally``.

Two modes (``$ACP2_MANUAL_MODE``):

* ``fixture`` (default) — the route handler serves **only** the exact paths in
  :data:`FIXTURE_ROUTE_TABLE`; any other ``/api/**`` request aborts the route
  and records the unexpected path (the scenario then FAILS naming it).  There
  is no catch-all ``200 {}`` fallback.
* ``live`` — no ``page.route`` interception at all: a real ``create_app`` runs
  on an ephemeral loopback port under a temporary run_root, seeds one COMPLETED
  job (with ``RESULT/result_manifest.json``) and one research proposal, and the
  page is served by that backend.  Panel-level wiring is out of scope
  (``NOT_VERIFIED``); direct HTTP calls to the seeded resources are verified.
  Missing routes fail loudly by name — never by falling back to fixtures.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from acp2_experience_scenarios import (
    EXIT_FAIL,
    _add_artifact,
    _assertion,
    _env_path,
    _finish,
    _node,
    _not_verified,
    _out_dir,
    _run,
    _tool_env,
    _truncate,
    _write_json,
    _write_text,
    register,
)

SCENARIO_ID = "manual-browser-fixtures"
FILTER = "acp2_manual"
MIN_TESTS = 8
NODE_SUITE_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed")
PROBE_JSON_RE = re.compile(r"ACP2_MANUAL_PROBE_JSON=(\{[^\n]*\})")
FRONTEND_ENTRY = "ACP_Workbench_v2.html"
PAGE_HTML_NAME = "manual-page.html"
CONSOLE_LOG_NAME = "manual-console.json"
NETWORK_LOG_NAME = "manual-network.json"
FORM_SHOT_NAME = "manual-form-full.png"
SCREENSHOT_NAME = "manual-browser-fixtures.png"
PROBE_LOG_NAME = "manual-browser.log"
PROBE_JSON_NAME = "manual-browser-fixtures.json"
CONSOLE_LOG_NAME = "manual-console.json"
NETWORK_LOG_NAME = "manual-network.json"
PAGE_HTML_NAME = "manual-page.html"
NODE_LOG_NAME = "manual-node-suite.log"

REQUIRED_PATHS = (
    "frontend/ACP_Workbench_v2.html",
    "frontend/js/workbench/features/science-question.js",
    "frontend/js/module_tests/acp2_manual.test.js",
    "frontend/js/module_tests/run_all.js",
)

MODE_ENV = "ACP2_MANUAL_MODE"
DEFAULT_MODE = "fixture"
MODES = ("fixture", "live")
ROUTE_TABLE_ENV = "ACP2_MANUAL_FIXTURE_ROUTES"

#: Exact API paths served in fixture mode, grouped by response body.  A path
#: appears in exactly one row.  The health row is an EXACT allowlist — the old
#: suffix match on ``/status`` shadowed the new
#: ``/api/v1/science/tasks/<id>/attempts/1/status`` route.
FIXTURE_ROUTE_TABLE: tuple[dict[str, Any], ...] = (
    {
        "key": "science_capabilities",
        "method": "GET",
        "paths": ("/api/v2/science/capabilities",),
    },
    {
        "key": "science_preflight",
        "method": "POST",
        "paths": ("/api/v2/science/preflight",),
    },
    {
        "key": "structures_parse",
        "method": "POST",
        "paths": ("/api/v1/structures/parse",),
    },
    {
        "key": "submit_forbidden",
        "method": "POST",
        "paths": ("/api/v1/jobs",),
    },
    {
        "key": "health",
        "method": "GET",
        "paths": ("/api/status", "/api/v1/status"),
    },
    {"key": "projects", "method": "GET", "paths": ("/api/v1/projects",)},
    {
        "key": "workflow_catalog",
        "method": "GET",
        "paths": ("/api/v1/workflow-catalog",),
    },
    {
        "key": "method_catalog",
        "method": "GET",
        "paths": ("/api/v1/method-catalog",),
    },
    {"key": "task_view", "method": "GET", "paths": ("/api/v2/task-view",)},
    {
        "key": "disk_usage",
        "method": "GET",
        "paths": ("/api/v1/maintenance/disk-usage",),
    },
)

HEALTH_PATHS = frozenset({"/api/status", "/api/v1/status"})


def fixture_route_key(method: str, url: str) -> str | None:
    """Pure fixture matcher: the allowlisted key for ``(method, url)`` else ``None``.

    Matching is exact on the query-stripped path.  A ``None`` result is a real
    failure in fixture mode (the probe aborts and names the path); it is never
    answered with a synthetic success body.
    """
    path = urlsplit(url).path or "/"
    verb = (method or "").upper()
    for row in FIXTURE_ROUTE_TABLE:
        if row["method"] == verb and path in row["paths"]:
            return str(row["key"])
    return None

# Executed by ``$ACP2_BROWSER_PYTHON``.  It serves frontend/ on an ephemeral
# loopback port, drives headless Chromium with fixture API routes and prints
# one machine-readable line; exit 0 only when every assertion held.
_BROWSER_PROBE = r"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

result = {
    "ok": False,
    "mode": "",
    "page_loaded": False,
    "fixture_interception": False,
    "unexpected_api_paths": [],
    "entry_visible": False,
    "demo_badge": False,
    "demo_badge_text": "",
    "submit_disabled_after_preflight_failure": False,
    "preflight_status_text": "",
    "zero_submit_calls": False,
    "submit_calls": 0,
    "screenshot_bytes": 0,
    "console_errors": [],
    "page_errors": [],
    "network": [],
    "port": 0,
    "chromium": "",
    "live": {},
}

frontend = Path(os.environ["ACP2_MANUAL_FRONTEND"]).resolve()
out_dir = Path(os.environ["ACP2_MANUAL_OUT"]).resolve()
screenshot = Path(os.environ["ACP2_MANUAL_SCREENSHOT"])
acceptance_dir = os.environ.get("ACP2_ACCEPTANCE_DIR", "")
if acceptance_dir and acceptance_dir not in sys.path:
    sys.path.insert(0, acceptance_dir)

mode = (os.environ.get("ACP2_MANUAL_MODE") or "fixture").strip().lower()
result["mode"] = mode
fixture_route_rows = json.loads(os.environ.get("ACP2_MANUAL_FIXTURE_ROUTES") or "[]")

FRONTEND_ENTRY = "ACP_Workbench_v2.html"
PAGE_HTML_NAME = "manual-page.html"
CONSOLE_LOG_NAME = "manual-console.json"
NETWORK_LOG_NAME = "manual-network.json"
FORM_SHOT_NAME = "manual-form-full.png"

CAPABILITIES_FIXTURE = {
    "protocol_version": "acp-science/1",
    "tools": [
        "acp_capabilities", "acp_preflight", "acp_submit", "acp_status",
        "acp_cancel", "acp_prepare_artifact", "acp_read_artifact", "acp_analyze",
    ],
    "capabilities": {
        "fixture": True,
        "questions": [
            {
                "question_id": "single_point_energy",
                "label": "单点能",
                "workflow": "singlepoint",
                "method_schema_id": "dft_singlepoint",
                "level_id": "single_point",
                "engine": "orca",
                "basis": "def2-TZVPP",
                "defaults": {
                    "method": {"value": "wB97M-V",
                     "source": "capability:single_point_energy.defaults.method"},
                    "charge": {"value": 0,
                     "source": "capability:single_point_energy.defaults.charge"},
                    "multiplicity": {"value": 1,
                     "source": "capability:single_point_energy.defaults.multiplicity"},
                    "solvent": {"value": "none",
                     "source": "capability:single_point_energy.defaults.solvent"},
                },
                "limitations": ["演示数据：fixture 入口，不代表真实平台提交"],
            },
            {
                "question_id": "geometry_optimization",
                "label": "几何优化",
                "workflow": "optimize",
                "method_schema_id": "dft_optimize",
                "level_id": "optimize",
                "engine": "orca",
                "basis": "",
                "defaults": {
                    "method": {"value": "r2SCAN-3c",
                     "source": "capability:geometry_optimization.defaults.method"},
                    "charge": {"value": 0,
                     "source": "capability:geometry_optimization.defaults.charge"},
                    "multiplicity": {"value": 1,
                     "source": "capability:geometry_optimization.defaults.multiplicity"},
                    "solvent": {"value": "none",
                     "source": "capability:geometry_optimization.defaults.solvent"},
                },
                "limitations": ["演示数据：fixture 入口，不代表真实平台提交"],
            },
        ],
    },
}

PARSE_FIXTURE = {
    "ok": True,
    "detected_format": "xyz",
    "errors": [],
    "warnings": [],
    "structures": [{"name": "demo_water", "atom_count": 3, "charge": 0, "multiplicity": 1}],
}

TASK_VIEW_FIXTURE = {
    "groups": [], "facets": {}, "total": 0, "counts": {},
    "offset": 0, "limit": 200, "next_offset": None,
}

# Response bodies keyed by the exact allowlist row (see the outer module's
# FIXTURE_ROUTE_TABLE).  No key means the path is not a fixture path.
FIXTURE_RESPONSES = {
    "science_capabilities": (200, CAPABILITIES_FIXTURE),
    "science_preflight": (
        200,
        {
            "executed": False,
            "metering_consumed": False,
            "transfers_attempted": False,
            "plan": {"question_id": "single_point_energy", "workflow": "singlepoint"},
            "estimate": {"feasible": False, "reason": "fixture preflight failure"},
        },
    ),
    "structures_parse": (200, PARSE_FIXTURE),
    "submit_forbidden": (
        500,
        {"detail": "submit must not be reached in this fixture run"},
    ),
    "health": (
        200,
        {
            "status": "ok",
            "host": "127.0.0.1",
            "port": 8765,
            "queue": {"queued": 0, "running": 0, "failed": 0},
        },
    ),
    "projects": (200, {"projects": []}),
    "workflow_catalog": (200, {"workflows": []}),
    "method_catalog": (
        200,
        {"method_schemas": {}, "method_meta": {}, "field_definitions": {}},
    ),
    "task_view": (200, TASK_VIEW_FIXTURE),
    "disk_usage": (
        200,
        {"percent_used": 1, "free_bytes": 1024, "job_count": 0, "cleanup_enabled": False},
    ),
}


class _StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, frontend: Path, **kwargs):
        self._frontend_root = frontend
        super().__init__(*args, directory=str(frontend), **kwargs)

    def log_message(self, *args):
        return

    def do_GET(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            self.path = "/" + FRONTEND_ENTRY
        elif path == "/favicon.ico":
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.end_headers()
            self.wfile.write(b"")
            return
        elif path.startswith("/api/"):
            self.send_response(404)
            self.end_headers()
            return
        return super().do_GET()


def _fixture_route(route, request, log, unexpected):
    url = request.url
    method = request.method
    log.append({"method": method, "url": url})
    path = urllib.parse.urlsplit(url).path or "/"

    key = None
    for row in fixture_route_rows:
        if row.get("method") == method.upper() and path in (row.get("paths") or ()):
            key = row.get("key")
            break
    if key is None or key not in FIXTURE_RESPONSES:
        # Fixture mode serves ONLY the explicit allowlist.  An unmatched API
        # request is a real defect: abort it and name the path so the scenario
        # FAILS instead of faking a 200 (the removed ``else: 200 {}`` branch).
        unexpected.append(path)
        route.abort("failed")
        return
    status, payload = FIXTURE_RESPONSES[key]
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _load_proposal_document(project_id, proposal_id):
    source_text = os.environ.get("ACP2_MANUAL_SOURCE", "")
    if source_text:
        fixture = (
            Path(source_text).resolve()
            / "docs/reference/next-change/acp2-contracts/experience/fixtures/proposal.valid.json"
        )
        try:
            document = json.loads(fixture.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = None
        if isinstance(document, dict):
            document = dict(document)
            document.pop("schema", None)
            document["project_id"] = project_id
            document["proposal_id"] = proposal_id
            return document
    return {
        "project_id": project_id,
        "proposal_id": proposal_id,
        "title": "Manual live fixture proposal",
    }


def _seed_live_backend(app, run_root):
    # Seed one COMPLETED job + one research proposal on the real backend.
    from acp.storage.manifest import ResultManifest
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        ResearchResourceRequest,
        ResearchResourceStore,
        research_resource_key,
    )
    from acp_services.store.records import JobRecord, JobSpec, JobStatus

    project_id = os.environ.get("ACP2_MANUAL_LIVE_PROJECT") or "uncategorized"
    job_id = "manual-live-completed"
    proposal_id = "prop-manual-live"
    work_dir = run_root / "manual-live" / "manual_live_water"
    (work_dir / "RESULT").mkdir(parents=True, exist_ok=True)
    (work_dir / "RESULT" / "opt.xyz").write_text(
        "2\nwater\nO 0 0 0\nH 0 0 0.96\n", encoding="utf-8"
    )
    manifest = ResultManifest(task_id=job_id, workflow="singlepoint", status="completed")
    manifest.add_product(
        id="optimized_structure",
        label="Optimized structure",
        path="opt.xyz",
        kind="structure",
    )
    manifest.write(work_dir / "RESULT")

    app.state.job_manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(
                workflow="singlepoint",
                name="manual_live_water",
                method={"method": "r2SCAN-3c"},
                project_id=project_id,
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=project_id,
            completed_at=_now_iso(),
            exit_code=0,
            result={"workflow": "singlepoint", "status": "completed"},
        )
    )

    store = getattr(app.state, "research_resources", None)
    owns_store = False
    if store is None:
        store = ResearchResourceStore(run_root / "acp_research.db")
        app.state.research_resources = store
        owns_store = True
    store.create(
        ResearchResourceRequest(
            resource_id=research_resource_key(project_id, "proposal", proposal_id),
            schema=PROPOSAL_SCHEMA,
            document=_load_proposal_document(project_id, proposal_id),
            principal_id="principal_local",
            project_id=project_id,
        )
    )
    return {
        "job_id": job_id,
        "proposal_id": proposal_id,
        "project_id": project_id,
        "research_store": store,
        "owns_research_store": owns_store,
    }


def _start_live_backend():
    # Real create_app on an ephemeral loopback port (no fixture server).
    import uvicorn

    run_root = Path(tempfile.mkdtemp(prefix="acp2-manual-live-run-root-"))
    # ``acp.api.server`` builds a module-level app at import time; pin its
    # run_root to the temp directory so no production path is ever resolved.
    os.environ["ACP_RUN_ROOT"] = str(run_root)
    from acp.api.server import create_app

    port = _free_port()
    app = create_app(run_root=run_root, host="127.0.0.1", port=port, max_running=1)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="acp2-manual-live", daemon=True)
    thread.start()
    deadline = time.monotonic() + 60.0
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("live backend thread exited before startup")
        if time.monotonic() > deadline:
            raise RuntimeError("live backend did not start within 60s")
        time.sleep(0.05)
    seeded = _seed_live_backend(app, run_root)
    return {
        "app": app,
        "server": server,
        "thread": thread,
        "run_root": run_root,
        "base_url": "http://127.0.0.1:%d" % port,
        "port": port,
        **seeded,
    }


def _stop_live_backend(live):
    # Strict teardown: stop uvicorn, close stores, remove the temp run_root.
    if not live:
        return
    try:
        server = live.get("server")
        if server is not None:
            server.should_exit = True
        thread = live.get("thread")
        if thread is not None:
            thread.join(timeout=20)
    finally:
        if live.get("owns_research_store") and live.get("research_store") is not None:
            try:
                live["research_store"].close()
            except Exception:  # noqa: BLE001 - teardown must never mask the verdict
                pass
        run_root = live.get("run_root")
        if run_root is not None:
            shutil.rmtree(run_root, ignore_errors=True)


def _live_direct_calls(page, live):
    project = urllib.parse.quote(str(live["project_id"]), safe="")
    proposal = urllib.parse.quote(str(live["proposal_id"]), safe="")
    job = urllib.parse.quote(str(live["job_id"]), safe="")
    targets = {
        "status": "/api/status",
        "science_capabilities": "/api/v2/science/capabilities?project_id=" + project,
        "research_proposal": (
            "/api/v2/research/proposals/%s?project_id=%s" % (proposal, project)
        ),
        "completed_job_status": (
            "/api/v1/science/tasks/%s/attempts/1/status?project_id=%s" % (job, project)
        ),
    }
    return page.evaluate(
        "async (targets) => {"
        " const out = {};"
        " for (const key of Object.keys(targets)) {"
        "  try {"
        "   const response = await fetch(targets[key], {headers: {Accept: 'application/json'}});"
        "   const text = await response.text();"
        "   let body = null;"
        "   try { body = JSON.parse(text); } catch (e) { body = null; }"
        "   out[key] = {status: response.status, body: body};"
        "  } catch (error) {"
        "   out[key] = {status: 0, error: String(error)};"
        "  }"
        " }"
        " return out;"
        "}",
        targets,
    )


def _live_checks(calls, live):
    def status_of(name):
        entry = calls.get(name) if isinstance(calls, dict) else None
        return entry.get("status") if isinstance(entry, dict) else None

    proposal_body = (calls.get("research_proposal") or {}).get("body")
    job_body = (calls.get("completed_job_status") or {}).get("body")
    checks = {
        "status": status_of("status") == 200,
        "science_capabilities": status_of("science_capabilities") == 200,
        "research_proposal": (
            status_of("research_proposal") == 200
            and isinstance(proposal_body, dict)
            and proposal_body.get("proposal_id") == live["proposal_id"]
        ),
        "completed_job_status": (
            status_of("completed_job_status") == 200
            and isinstance(job_body, dict)
            and job_body.get("status") == "completed"
        ),
    }
    missing = sorted(
        {
            name
            for name in checks
            if status_of(name) in (404, 405)
        }
    )
    return checks, missing


def main() -> int:
    from playwright.sync_api import sync_playwright

    try:
        from browser_runtime import chromium_launch_options
    except ImportError:
        def chromium_launch_options(*, no_sandbox: bool = False):  # type: ignore[misc]
            args = ["--use-angle=vulkan", "--use-vulkan=swiftshader"]
            if no_sandbox:
                args.append("--no-sandbox")
            return {"headless": True, "args": args}

    live = None
    httpd = None
    if mode == "live":
        try:
            live = _start_live_backend()
        except Exception as exc:  # noqa: BLE001 - a missing bootstrap piece must FAIL loudly
            result["error"] = f"live_backend_bootstrap_failed: {type(exc).__name__}: {exc}"
            traceback.print_exc()
            print("ACP2_MANUAL_PROBE_JSON=" + json.dumps(result, ensure_ascii=False), flush=True)
            return 1
        result["port"] = int(live["port"])
        result["live"] = {
            "run_root": str(live["run_root"]),
            "project_id": live["project_id"],
            "job_id": live["job_id"],
            "proposal_id": live["proposal_id"],
            "direct_calls": {},
        }
        page_url = live["base_url"] + "/"
        print(f"live_backend={live['base_url']} run_root={live['run_root']}", flush=True)
    else:
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(_StaticHandler, frontend=frontend)
        )
        server_thread = Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        result["port"] = int(httpd.server_address[1])
        page_url = "http://127.0.0.1:%d/" % result["port"]
        print(f"static_server={page_url} root={frontend}", flush=True)

    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            options = chromium_launch_options(no_sandbox=True)
            if "env" in options:
                options = dict(options)
                options.pop("env", None)
            browser = playwright.chromium.launch(**options)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            result["chromium"] = browser.version
            console_messages = []
            page_errors = []
            network_log = []

            def on_console(message):
                console_messages.append({"type": message.type, "text": message.text})
                if message.type == "error":
                    result["console_errors"].append(message.text)

            page.on("console", on_console)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", lambda request: network_log.append(
                {"phase": "request", "method": request.method, "url": request.url}))
            page.on("response", lambda response: network_log.append(
                {"phase": "response", "status": response.status, "url": response.url}))

            if mode == "fixture":
                page.route(
                    "**/api/**",
                    lambda route: _fixture_route(
                        route, route.request, network_log, result["unexpected_api_paths"]
                    ),
                )
                result["fixture_interception"] = True

            page.goto(page_url, wait_until="load")
            result["page_loaded"] = page.evaluate("() => document.readyState") == "complete"

            if mode == "fixture":
                entry = page.locator("#science-question-panel")
                entry.wait_for(state="visible", timeout=20000)
                result["entry_visible"] = entry.is_visible()

                badge = page.locator("#science-question-demo-badge")
                badge.wait_for(state="visible", timeout=20000)
                result["demo_badge_text"] = badge.inner_text().strip()
                result["demo_badge"] = result["demo_badge_text"] == "演示数据"

                page.locator("#sq-load-demo").click()
                page.wait_for_function(
                    "() => { const el = document.getElementById('sq-structure');"
                    " return el && el.value && el.value.indexOf('water demo') !== -1; }",
                    timeout=10000,
                )

                page.locator("#sq-preflight").click()
                page.wait_for_function(
                    "() => { const el = document.getElementById('sq-status');"
                    " return el && el.textContent && el.textContent.indexOf('预检失败') !== -1; }",
                    timeout=15000,
                )
                result["preflight_status_text"] = page.locator("#sq-status").inner_text().strip()
                submit_button = page.locator("#sq-submit")
                result["submit_disabled_after_preflight_failure"] = submit_button.is_disabled()
            else:
                try:
                    result["entry_visible"] = page.locator(
                        "#science-question-panel"
                    ).is_visible()
                except Exception:  # noqa: BLE001 - panel wiring is out of live scope
                    result["entry_visible"] = False
                calls = _live_direct_calls(page, live)
                checks, missing = _live_checks(calls, live)
                result["live"]["direct_calls"] = calls
                result["live"]["checks"] = checks
                result["live"]["missing_routes"] = missing
                result["live"]["panel_status"] = "NOT_VERIFIED"

            result["submit_calls"] = sum(
                1 for item in network_log
                if item.get("phase") == "request" and item.get("method") == "POST"
                and item.get("url", "").split("?", 1)[0].endswith("/jobs")
            )
            result["zero_submit_calls"] = result["submit_calls"] == 0

            # Evidence capture: park the panel scroll at the top so the form
            # (question select, structure, defaults) is the part the full-page
            # shot shows; then also capture the unclipped form as a second shot.
            page.evaluate(
                "() => { const el = document.getElementById('science-question-body');"
                " if (el) el.scrollTop = 0; }"
            )
            page.screenshot(path=str(screenshot), full_page=True)
            result["screenshot_bytes"] = screenshot.stat().st_size if screenshot.is_file() else 0
            page.add_style_tag(content=(
                ".science-question-panel{max-height:none !important;}"
                ".science-question-panel .panel-body{overflow:visible !important;}"
                "#right-col-zone{overflow:visible !important;}"
            ))
            page.wait_for_timeout(100)
            # Element capture: the panel is taller than the column's clip box,
            # so the form evidence is the panel's own bounding box.
            try:
                page.locator("#science-question-panel").screenshot(
                    path=str(out_dir / FORM_SHOT_NAME)
                )
            except Exception:  # noqa: BLE001 - form evidence is optional in live mode
                if mode == "fixture":
                    raise
            (out_dir / PAGE_HTML_NAME).write_text(page.content(), encoding="utf-8")
            (out_dir / CONSOLE_LOG_NAME).write_text(
                json.dumps({"messages": console_messages, "page_errors": page_errors},
                           indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (out_dir / NETWORK_LOG_NAME).write_text(
                json.dumps(network_log, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            result["page_errors"] = page_errors
            result["network"] = network_log[:200]
            if mode == "fixture":
                result["ok"] = (
                    result.get("entry_visible") is True
                    and result.get("demo_badge") is True
                    and result.get("submit_disabled_after_preflight_failure") is True
                    and result.get("zero_submit_calls") is True
                    and int(result.get("screenshot_bytes") or 0) > 0
                    and not result.get("console_errors")
                    and not result.get("page_errors")
                    and not result.get("unexpected_api_paths")
                )
            else:
                result["ok"] = (
                    result.get("page_loaded") is True
                    and int(result.get("screenshot_bytes") or 0) > 0
                    and all(result["live"]["checks"].values())
                )
            context.close()
            context = None
            browser.close()
            browser = None
    except Exception as exc:  # noqa: BLE001 - every probe failure must be FAIL
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        # The browser, the static server and the live backend are always
        # closed here, on both the happy and the failing path.
        for closer in (
            lambda: context.close() if context is not None else None,
            lambda: browser.close() if browser is not None else None,
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001 - teardown must never mask the verdict
                pass
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        _stop_live_backend(live)

    print("ACP2_MANUAL_PROBE_JSON=" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"probe crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
"""


def _probe_payload(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith("ACP2_MANUAL_PROBE_JSON="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def _isolated_root() -> Path | None:
    for variable in ("ACP2_DATA_ROOT", "ACP2_TOOLS"):
        value = os.environ.get(variable)
        if value:
            return Path(value).resolve()
    return None


def _safe_run_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-._" else "_" for char in value)
    return cleaned[:120] or "run"


def scenario_manual_browser_fixtures(context: Any) -> int:
    mode = (os.environ.get(MODE_ENV) or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        _assertion(
            context,
            "manual.mode",
            False,
            f"{MODE_ENV}={mode!r} allowed={list(MODES)}",
        )
        context.reason = f"invalid_manual_mode:{mode}"
        return EXIT_FAIL
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    missing = [rel for rel in REQUIRED_PATHS if not (source / rel).is_file()]
    if missing:
        return _not_verified(
            context, "missing_prerequisite:manual_entry_absent:" + ",".join(missing)
        )

    out = _out_dir(context)
    timeout = max(30.0, float(context.timeout))
    env = _tool_env(node)
    runner = source / "frontend/js/module_tests/run_all.js"

    # ---- Phase 1: the offline module row ---------------------------------
    node_result = _run([node, str(runner), FILTER], cwd=source, timeout=timeout, env=env)
    node_combined = (node_result.stdout or "") + (node_result.stderr or "")
    match = NODE_SUITE_RE.search(node_combined)
    passed = int(match.group(1)) if match else 0
    failed = int(match.group(2)) if match else 0
    _assertion(
        context,
        "manual.node_suite",
        node_result.returncode == 0 and failed == 0 and passed >= MIN_TESTS,
        f"rc={node_result.returncode} passed={passed} failed={failed} min={MIN_TESTS}",
    )
    _write_text(out, NODE_LOG_NAME, _truncate(node_combined, 16000))
    _add_artifact(context, NODE_LOG_NAME)

    # ---- Phase 2: the real-browser fixture row ---------------------------
    interpreter = os.environ.get("ACP2_BROWSER_PYTHON")
    if not interpreter:
        return _not_verified(context, "browser_python_absent:ACP2_BROWSER_PYTHON_unset")
    interpreter_path = Path(interpreter)
    if not interpreter_path.is_file():
        return _not_verified(context, f"browser_python_absent:{interpreter_path}")
    browsers_path_text = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path_text:
        return _not_verified(context, "chromium_not_installed:PLAYWRIGHT_BROWSERS_PATH_unset")
    if not Path(browsers_path_text).resolve().is_dir():
        return _not_verified(
            context, f"chromium_not_installed:PLAYWRIGHT_BROWSERS_PATH_absent:{browsers_path_text}"
        )
    root = _isolated_root()
    if root is None:
        return _not_verified(context, "isolation_root_absent:ACP2_DATA_ROOT/ACP2_TOOLS")

    run_dir = root / "browser-runtime" / _safe_run_id(str(context.run_id))
    profile = run_dir / "user-data"
    cache = run_dir / "cache"
    home = run_dir / "home"
    tmp = run_dir / "tmp"
    for directory in (profile, cache, home, tmp):
        directory.mkdir(parents=True, exist_ok=True)

    browser_env = dict(os.environ)
    browser_env.update(
        {
            "PLAYWRIGHT_BROWSERS_PATH": str(Path(browsers_path_text).resolve()),
            "HOME": str(home),
            "XDG_CACHE_HOME": str(cache),
            "XDG_CONFIG_HOME": str(run_dir / "config"),
            "XDG_DATA_HOME": str(run_dir / "data"),
            "TMPDIR": str(tmp),
            "ACP2_MANUAL_FRONTEND": str(source / "frontend"),
            "ACP2_MANUAL_OUT": str(out),
            "ACP2_MANUAL_SCREENSHOT": str(out / SCREENSHOT_NAME),
            "ACP2_MANUAL_SOURCE": str(source),
            "ACP2_MANUAL_MODE": mode,
            "ACP2_MANUAL_FIXTURE_ROUTES": json.dumps(FIXTURE_ROUTE_TABLE),
            "ACCEPTANCE_DIR": str(Path(__file__).resolve().parent),
            "ACP2_ACCEPTANCE_DIR": str(Path(__file__).resolve().parent),
        }
    )
    if mode == "live":
        # Live mode must reach the real backend on the page's own origin.
        browser_env["ACP_PUBLIC_ORIGIN"] = ""
        browser_env["ACP_DEPLOYMENT_TIER"] = "local-single"
    probe_timeout = max(30.0, min(timeout - 20.0, 240.0))
    probe = _run(
        [interpreter, "-c", _BROWSER_PROBE],
        cwd=str(run_dir),
        timeout=probe_timeout,
        env=browser_env,
    )
    probe_combined = (probe.stdout or "") + (probe.stderr or "")
    _write_text(out, PROBE_LOG_NAME, _truncate(probe_combined, 24000))
    _add_artifact(context, PROBE_LOG_NAME)

    payload = _probe_payload(probe_combined) or {}
    screenshot = out / SCREENSHOT_NAME
    screenshot_bytes = screenshot.stat().st_size if screenshot.is_file() else 0
    console_errors = list(payload.get("console_errors") or [])
    page_errors = list(payload.get("page_errors") or [])
    network = list(payload.get("network") or [])

    _assertion(
        context,
        "manual.browser.probe_completed",
        probe.returncode == 0 and bool(payload) and "error" not in payload,
        f"rc={probe.returncode} mode={payload.get('mode')} "
        f"error={payload.get('error')} port={payload.get('port')}",
    )
    _assertion(
        context,
        "manual.browser.screenshot_nonempty",
        screenshot_bytes > 0 and int(payload.get("screenshot_bytes") or 0) == screenshot_bytes,
        f"screenshot_bytes={screenshot_bytes} probe_bytes={payload.get('screenshot_bytes')}",
    )
    _assertion(
        context,
        "manual.browser.network_log",
        bool(network),
        f"entries={len(network)}",
    )
    if mode == "fixture":
        _assertion(
            context,
            "manual.browser.entry_visible",
            payload.get("entry_visible") is True,
            f"entry_visible={payload.get('entry_visible')}",
        )
        _assertion(
            context,
            "manual.browser.demo_badge",
            payload.get("demo_badge") is True and payload.get("demo_badge_text") == "演示数据",
            f"demo_badge={payload.get('demo_badge')} text={payload.get('demo_badge_text')!r}",
        )
        _assertion(
            context,
            "manual.browser.submit_disabled_after_preflight_failure",
            payload.get("submit_disabled_after_preflight_failure") is True
            and "预检失败" in str(payload.get("preflight_status_text") or ""),
            f"disabled={payload.get('submit_disabled_after_preflight_failure')} "
            f"status={payload.get('preflight_status_text')!r}",
        )
        _assertion(
            context,
            "manual.browser.zero_submit_calls",
            payload.get("zero_submit_calls") is True and payload.get("submit_calls") == 0,
            f"submit_calls={payload.get('submit_calls')}",
        )
        unexpected = list(payload.get("unexpected_api_paths") or [])
        _assertion(
            context,
            "manual.browser.fixture_allowlist_only",
            not unexpected,
            f"unexpected_api_paths={unexpected}",
        )
        _assertion(
            context,
            "manual.browser.zero_console_errors",
            not console_errors and not page_errors,
            f"console_errors={len(console_errors)} page_errors={len(page_errors)} "
            + _truncate(" | ".join(console_errors + page_errors), 800),
        )
    else:
        live_payload = payload.get("live") or {}
        live_checks = live_payload.get("checks") or {}
        live_calls = live_payload.get("direct_calls") or {}
        missing_routes = list(live_payload.get("missing_routes") or [])
        _assertion(
            context,
            "manual.live.page_loaded",
            payload.get("page_loaded") is True,
            f"page_loaded={payload.get('page_loaded')} port={payload.get('port')}",
        )
        _assertion(
            context,
            "manual.live.no_fixture_interception",
            payload.get("fixture_interception") is False,
            f"fixture_interception={payload.get('fixture_interception')}",
        )
        for name in (
            "status",
            "science_capabilities",
            "research_proposal",
            "completed_job_status",
        ):
            entry = live_calls.get(name) or {}
            _assertion(
                context,
                f"manual.live.direct_call.{name}",
                live_checks.get(name) is True,
                f"status={entry.get('status')} missing_routes={missing_routes}",
            )

    if screenshot.is_file():
        _add_artifact(context, SCREENSHOT_NAME)
    for name in (CONSOLE_LOG_NAME, NETWORK_LOG_NAME, PAGE_HTML_NAME, FORM_SHOT_NAME):
        if (out / name).is_file():
            _add_artifact(context, name)

    summary = {
        "scenario": SCENARIO_ID,
        "filter": FILTER,
        "mode": mode,
        "node": {
            "command": [node, str(runner), FILTER],
            "exit_code": node_result.returncode,
            "passed": passed,
            "failed": failed,
            "min_tests": MIN_TESTS,
        },
        "browser": {
            "interpreter": interpreter,
            "exit_code": probe.returncode,
            "payload": {key: value for key, value in payload.items() if key != "network"},
            "network_entries": len(network),
        },
    }
    _write_json(out, PROBE_JSON_NAME, summary)
    _add_artifact(context, PROBE_JSON_NAME)

    context.extensions.update(
        {
            "experience.manual_mode": mode,
            "experience.manual_suite_passed": passed,
            "experience.manual_suite_failed": failed,
            "experience.manual_min_tests": MIN_TESTS,
            "experience.manual_entry_visible": bool(payload.get("entry_visible")),
            "experience.manual_demo_badge": bool(payload.get("demo_badge")),
            "experience.manual_submit_disabled_after_preflight": bool(
                payload.get("submit_disabled_after_preflight_failure")
            ),
            "experience.manual_submit_calls": payload.get("submit_calls"),
            "experience.manual_console_errors": len(console_errors) + len(page_errors),
            "experience.manual_screenshot_bytes": screenshot_bytes,
            "experience.manual_chromium": payload.get("chromium"),
            "experience.manual_unexpected_api_paths": list(
                payload.get("unexpected_api_paths") or []
            ),
        }
    )
    if mode == "live":
        live_payload = payload.get("live") or {}
        context.extensions.update(
            {
                "experience.manual_live_panel_status": "NOT_VERIFIED",
                "experience.manual_live_checks": live_payload.get("checks"),
                "experience.manual_live_missing_routes": list(
                    live_payload.get("missing_routes") or []
                ),
                "experience.manual_live_run_root": live_payload.get("run_root"),
                "experience.manual_live_job_id": live_payload.get("job_id"),
                "experience.manual_live_proposal_id": live_payload.get("proposal_id"),
            }
        )

    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    if failures:
        context.reason = "manual_browser_fixtures_assertion_failed"
        return EXIT_FAIL
    return _finish(context, "manual_browser_fixtures_assertion_failed")


register(SCENARIO_ID, scenario_manual_browser_fixtures)
