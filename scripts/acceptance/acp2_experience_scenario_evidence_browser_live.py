"""``evidence-browser-live`` — scientific evidence view, real browser.

This body is the T-31 / C21 acceptance row.  Two levels, as the plan freezes
them:

* **fixture row** (default, ``--mode fixture``, ``evidence_kind=fixture``,
  public phase ``experience:fixture``): the zero-dependency Node module row
  (``frontend/js/module_tests/run_all.js acp2_evidence``, the claim-state
  naming matrix) plus a REAL headless Chromium (Playwright, frozen browsers
  root) loading the Workbench page served from ``frontend/`` on an ephemeral
  loopback port (never 8765, never the production service) with the frozen
  EvidenceReport/ViewModel fixture injected.  The probe asserts the four claim
  states render distinctly, ``inconclusive`` shows the visible
  ``live not verified`` state and is never success-colored, execution COMPLETED
  sits next to (never inside) the claim state, a claim expands to raw artifact
  refs / validator / method-version / missing validation / RPH decision
  source, the energy chart exposes only save-as-candidate (zero ``POST /jobs``),
  hide/show/switch-task/dispose behave (``dispose()`` then ``tick()`` fires no
  callback), zero console errors, and a non-empty screenshot.
* **live row** (``--mode live``, ``evidence_kind=live``, public phase
  ``experience:live``): runs ONLY on T-30a's real research round.  Without a
  ``PASS`` ``experience:control`` research-loop-live receipt and a consumable
  real evidence bundle the row is ``NOT_VERIFIED(upstream_not_delivered)``
  (exit 2).  Nothing is fabricated to make the live row pass.

Also asserted in both rows: ``fetch(`` is absent from every
``frontend/js/visualization/*`` module (ACV never calls the API) and from the
feature module.

A missing prerequisite (Node, source tree, browser interpreter, Chromium) is
``NOT_VERIFIED`` (exit 2); a command that ran and produced a wrong result is
``FAIL`` (exit 1).  Browser, profile, cache, HOME and TMPDIR all live under
``$ACP2_DATA_ROOT``/``$ACP2_TOOLS``; every server/browser process is closed in
``finally``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

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
    publish_observations,
    register,
)

SCENARIO_ID = "evidence-browser-live"
FILTER = "acp2_evidence"
MIN_TESTS = 10
NODE_SUITE_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed")
PROBE_JSON_RE = re.compile(r"ACP2_EVIDENCE_PROBE_JSON=(\{[^\n]*\})")

FRONTEND_ENTRY = "ACP_Workbench_v2.html"
FEATURE_REL = "frontend/js/workbench/features/scientific-evidence.js"
FIXTURE_REL = "frontend/js/workbench/contracts/fixtures/evidence-bundle.js"
VIZ_REL = "frontend/js/visualization"
NODE_LOG_NAME = "evidence-node-suite.log"
PROBE_LOG_NAME = "evidence-browser.log"
PROBE_JSON_NAME = "evidence-browser-live.json"
SCREENSHOT_NAME = "evidence-browser-live.png"
PANEL_SHOT_NAME = "evidence-panel.png"
CONSOLE_LOG_NAME = "evidence-console.json"
NETWORK_LOG_NAME = "evidence-network.json"
PAGE_HTML_NAME = "evidence-page.html"

REQUIRED_PATHS = (
    "frontend/ACP_Workbench_v2.html",
    FEATURE_REL,
    FIXTURE_REL,
    "frontend/js/module_tests/acp2_evidence.test.js",
    "frontend/js/module_tests/run_all.js",
)

# Executed by ``$ACP2_BROWSER_PYTHON``.  Serves frontend/ on an ephemeral
# loopback port, injects the frozen evidence bundle before any page script and
# drives headless Chromium through the mount/expand/energy/hide/switch/dispose
# matrix.  Exit 0 only when every assertion held.
_BROWSER_PROBE = r"""
import json
import os
import sys
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

result = {
    "ok": False,
    "entry_visible": False,
    "claim_states": [],
    "four_states_distinct": False,
    "inconclusive_marker": False,
    "inconclusive_not_success_colored": False,
    "execution_next_to_claim": False,
    "expand_artifacts": False,
    "expand_validator": False,
    "expand_method_version": False,
    "expand_missing_validation": False,
    "expand_decision_source": False,
    "energy_save_candidate_only": False,
    "energy_save_clicks": 0,
    "submit_calls": 0,
    "hide_pauses_ticks": False,
    "switch_task_clears_claims": False,
    "dispose_then_tick_fires_nothing": False,
    "dispose_detail": {},
    "screenshot_bytes": 0,
    "console_errors": [],
    "page_errors": [],
    "network": [],
    "port": 0,
    "chromium": "",
    "evidence_kind": "",
}

frontend = Path(os.environ["ACP2_EVIDENCE_FRONTEND"]).resolve()
out_dir = Path(os.environ["ACP2_EVIDENCE_OUT"]).resolve()
screenshot = Path(os.environ["ACP2_EVIDENCE_SCREENSHOT"])
bundle_file = Path(os.environ["ACP2_EVIDENCE_BUNDLE"]).resolve()
acceptance_dir = os.environ.get("ACP2_ACCEPTANCE_DIR", "")
if acceptance_dir and acceptance_dir not in sys.path:
    sys.path.insert(0, acceptance_dir)

PAGE_HTML_NAME = "evidence-page.html"
CONSOLE_LOG_NAME = "evidence-console.json"
NETWORK_LOG_NAME = "evidence-network.json"
PANEL_SHOT_NAME = "evidence-panel.png"

TASK_VIEW_FIXTURE = {
    "groups": [], "facets": {}, "total": 0, "counts": {},
    "offset": 0, "limit": 200, "next_offset": None,
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
            self.path = "/" + "ACP_Workbench_v2.html"
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


def _fixture_route(route, request, log):
    url = request.url
    method = request.method
    log.append({"method": method, "url": url})
    path = url.split("?", 1)[0]

    def fulfill(status, payload):
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    if path.endswith("/status"):
        fulfill(200, {"status": "ok", "host": "127.0.0.1", "port": 8765,
                      "queue": {"queued": 0, "running": 0, "failed": 0}})
    elif path.endswith("/projects"):
        fulfill(200, {"projects": []})
    elif path.endswith("/workflow-catalog"):
        fulfill(200, {"workflows": []})
    elif path.endswith("/method-catalog"):
        fulfill(200, {"method_schemas": {}, "method_meta": {}, "field_definitions": {}})
    elif path.endswith("/task-view"):
        fulfill(200, TASK_VIEW_FIXTURE)
    elif path.endswith("/maintenance/disk-usage"):
        fulfill(200, {"percent_used": 1, "free_bytes": 1024,
                      "job_count": 0, "cleanup_enabled": False})
    elif path.endswith("/jobs") and method == "POST":
        fulfill(500, {"detail": "submit must not be reached from the evidence view"})
    else:
        fulfill(200, {})


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

    bundle_source = bundle_file.read_text(encoding="utf-8")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_StaticHandler, frontend=frontend))
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
            page.route("**/api/**", lambda route: _fixture_route(route, route.request, network_log))

            # Frozen EvidenceReport/ViewModel fixture, present before any page
            # script runs: the feature's DOMContentLoaded mount adopts it.
            page.add_init_script(bundle_source)
            page.goto(page_url, wait_until="load")

            entry = page.locator("#scientific-evidence-panel")
            entry.wait_for(state="visible", timeout=20000)
            result["entry_visible"] = entry.is_visible()

            page.wait_for_function(
                "() => { const el = document.querySelector('[data-se-claims]');"
                " return el && el.querySelectorAll('.se-claim').length === 4; }",
                timeout=20000,
            )
            result["claim_states"] = page.evaluate(
                "() => Array.from(document.querySelectorAll('.se-claim'))"
                ".map((el) => el.getAttribute('data-claim-status'))"
            )
            expected = ["supported", "refuted", "inconclusive", "not_evaluated"]
            result["four_states_distinct"] = sorted(result["claim_states"]) == sorted(expected)

            result["inconclusive_marker"] = page.evaluate(
                "() => { const el = document.querySelector('.se-live-unverified');"
                " return !!el && el.textContent.indexOf('live not verified') !== -1; }"
            )
            result["inconclusive_not_success_colored"] = page.evaluate(
                "() => { const row = document.querySelector("
                "'.se-claim[data-claim-status=\"inconclusive\"]');"
                " if (!row) return false;"
                " const inc = row.querySelector('.se-state-inconclusive');"
                " const sup = document.querySelector('.se-state-supported');"
                " if (!inc || !sup) return false;"
                " const incColor = getComputedStyle(inc).backgroundColor;"
                " const supColor = getComputedStyle(sup).backgroundColor;"
                " return inc.className.indexOf('se-state-supported') === -1"
                "   && incColor !== supColor; }"
            )
            result["execution_next_to_claim"] = page.evaluate(
                "() => { const row = document.querySelector("
                "'.se-claim[data-claim-status=\"inconclusive\"]');"
                " if (!row) return false;"
                " const exec = row.querySelector('[data-exec-state]');"
                " const claim = row.querySelector("
                "'[data-claim-state-badge=\"inconclusive\"]');"
                " return !!exec && exec.getAttribute('data-exec-state') === 'completed'"
                "   && !!claim; }"
            )

            page.locator('[data-se-claim-toggle="claim_ts_mode"]').click()
            page.wait_for_function(
                "() => { const d = document.querySelector("
                "'[data-se-detail=\"claim_ts_mode\"]');"
                " return d && d.getAttribute('hidden') === null; }",
                timeout=10000,
            )
            result["expand_artifacts"] = page.evaluate(
                "() => { const d = document.querySelector("
                "'[data-se-detail=\"claim_ts_mode\"]');"
                " return !!d && !!d.querySelector('[data-se-artifact-path]'); }"
            )
            result["expand_validator"] = page.evaluate(
                "() => { const d = document.querySelector("
                "'[data-se-detail=\"claim_ts_mode\"]');"
                " return !!d && !!d.querySelector('[data-se-validator-id]'); }"
            )
            result["expand_method_version"] = page.evaluate(
                "() => { const d = document.querySelector("
                "'[data-se-detail=\"claim_ts_mode\"]');"
                " return !!d && !!d.querySelector('[data-se-method-field=\"method\"]')"
                "   && d.textContent.indexOf('6.0.1') !== -1; }"
            )
            page.locator('[data-se-claim-toggle="claim_mechanism_pathway"]').click()
            page.wait_for_function(
                "() => { const d = document.querySelector("
                "'[data-se-detail=\"claim_mechanism_pathway\"]');"
                " return d && d.getAttribute('hidden') === null; }",
                timeout=10000,
            )
            result["expand_missing_validation"] = page.evaluate(
                "() => { const d = document.querySelector("
                "'[data-se-detail=\"claim_mechanism_pathway\"]');"
                " return !!d && !!d.querySelector('[data-se-check-id=\"missing_irc\"]'); }"
            )
            result["expand_decision_source"] = page.evaluate(
                "() => { const d = document.querySelector("
                "'[data-se-detail=\"claim_mechanism_pathway\"]');"
                " return !!d && !!d.querySelector('[data-se-decision-source]'); }"
            )

            result["energy_save_candidate_only"] = page.evaluate(
                "() => { const actions = document.querySelector('[data-se-energy-actions]');"
                " if (!actions) return false;"
                " const buttons = Array.from(actions.querySelectorAll('button'));"
                " return buttons.length === 1"
                "   && buttons[0].getAttribute('data-se-action') === 'save-candidate'"
                "   && window.ACPScientificEvidence.getState().energyActions.length === 1"
                "   && window.ACPScientificEvidence.getState().energyActions[0]"
                "       === 'save-candidate'; }"
            )
            page.locator('[data-se-action="save-candidate"]').click()
            result["energy_save_clicks"] = page.evaluate(
                "() => window.ACPScientificEvidence.getState().savedCandidates.length"
            )
            result["submit_calls"] = sum(
                1 for item in network_log
                if item.get("phase") == "request" and item.get("method") == "POST"
                and item.get("url", "").split("?", 1)[0].endswith("/jobs")
            )

            result["hide_pauses_ticks"] = page.evaluate(
                "() => { const api = window.ACPScientificEvidence;"
                " api.registerTick(() => {});"
                " api.hide();"
                " const hiddenRan = api.tick();"
                " const frozen = api.getState().callbacksFired;"
                " const hiddenAgain = api.tick();"
                " api.show();"
                " const shown = api.tick();"
                " return hiddenRan === 0 && hiddenAgain === 0 && shown >= 1"
                "   && api.getState().callbacksFired > frozen; }"
            )

            page.screenshot(path=str(screenshot), full_page=True)
            result["screenshot_bytes"] = screenshot.stat().st_size if screenshot.is_file() else 0
            page.locator("#scientific-evidence-panel").screenshot(
                path=str(out_dir / PANEL_SHOT_NAME)
            )
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

            result["switch_task_clears_claims"] = page.evaluate(
                "() => { const api = window.ACPScientificEvidence;"
                " api.switchTask({ job_id: 'probe_other', attempt: 2 });"
                " const state = api.getState();"
                " return state.claims.length === 0 && state.claimStates.length === 0"
                "   && !!document.querySelector('[data-se-empty]'); }"
            )

            result["dispose_detail"] = page.evaluate(
                "() => { const api = window.ACPScientificEvidence;"
                " const before = api.getState().callbacksFired;"
                " api.dispose();"
                " const ran = api.tick();"
                " let lateError = null;"
                " try { api.registerTick(() => { lateError = 'late callback ran'; }); }"
                " catch (e) { lateError = String(e); }"
                " const ranAfterRegister = api.tick();"
                " return { before: before, ran: ran, ranAfterRegister: ranAfterRegister,"
                "   after: api.getState().callbacksFired, disposed: api.getState().disposed,"
                "   lateError: lateError }; }"
            )
            page.wait_for_timeout(120)
            detail = result["dispose_detail"] or {}
            result["dispose_then_tick_fires_nothing"] = (
                detail.get("disposed") is True
                and detail.get("ran") == 0
                and detail.get("ranAfterRegister") == 0
                and detail.get("after") == detail.get("before")
                and not detail.get("lateError")
            )

            result["page_errors"] = page_errors
            result["network"] = network_log[:200]
            result["ok"] = (
                result.get("entry_visible") is True
                and result.get("four_states_distinct") is True
                and result.get("inconclusive_marker") is True
                and result.get("inconclusive_not_success_colored") is True
                and result.get("execution_next_to_claim") is True
                and result.get("expand_artifacts") is True
                and result.get("expand_validator") is True
                and result.get("expand_method_version") is True
                and result.get("expand_missing_validation") is True
                and result.get("expand_decision_source") is True
                and result.get("energy_save_candidate_only") is True
                and int(result.get("energy_save_clicks") or 0) >= 1
                and int(result.get("submit_calls") or 0) == 0
                and result.get("hide_pauses_ticks") is True
                and result.get("switch_task_clears_claims") is True
                and result.get("dispose_then_tick_fires_nothing") is True
                and int(result.get("screenshot_bytes") or 0) > 0
                and not result.get("console_errors")
                and not result.get("page_errors")
            )
            context.close()
            context = None
            browser.close()
            browser = None
    except Exception as exc:  # noqa: BLE001 - every probe failure must be FAIL
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        for closer in (
            lambda: context.close() if context is not None else None,
            lambda: browser.close() if browser is not None else None,
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001 - teardown must never mask the verdict
                pass
        httpd.shutdown()
        httpd.server_close()

    print("ACP2_EVIDENCE_PROBE_JSON=" + json.dumps(result, ensure_ascii=False), flush=True)
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
        if line.startswith("ACP2_EVIDENCE_PROBE_JSON="):
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


def _viz_fetch_scan(source: Path) -> tuple[int, list[str]]:
    """grep -R "fetch(" frontend/js/visualization/ — ACV never calls the API."""

    hits: list[str] = []
    viz_dir = source / VIZ_REL
    if not viz_dir.is_dir():
        return -1, hits
    for path in sorted(viz_dir.glob("*.js")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "fetch(" in line:
                hits.append(f"{path.name}:{number}")
    return len(hits), hits


def _research_round_receipt() -> Path | None:
    """T-30a gate: a PASS ``experience:control`` research-loop-live receipt."""

    root = _env_path("ACP2_EVIDENCE")
    if root is None:
        return None
    base = root / "C" / "research-loop-live"
    if not base.is_dir():
        return None
    for receipt in sorted(base.rglob("*.json")):
        if not receipt.name.startswith("experience_"):
            continue
        try:
            document = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        if document.get("scenario") != "experience:research-loop-live":
            continue
        if document.get("result") != "PASS":
            continue
        if document.get("phase") == "experience:control":
            return receipt
    return None


def _live_bundle(receipt: Path) -> Path | None:
    """A consumable real evidence bundle next to the T-30a round evidence."""

    for candidate in sorted(receipt.parent.rglob("evidence-bundle*.json")):
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(document, dict) and document.get("evidence_report") and document.get("view"):
            return candidate
    return None


def _run_rows(context: Any, bundle_path: Path, mode: str) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    missing = [rel for rel in REQUIRED_PATHS if not (source / rel).is_file()]
    if missing:
        return _not_verified(
            context, "missing_prerequisite:evidence_view_absent:" + ",".join(missing)
        )
    if not bundle_path.is_file():
        return _not_verified(context, f"missing_prerequisite:evidence_bundle_absent:{bundle_path}")

    out = _out_dir(context)
    timeout = max(30.0, float(context.timeout))
    env = _tool_env(node)
    runner = source / "frontend/js/module_tests/run_all.js"

    # ---- Row 1: offline module matrix -----------------------------------
    node_result = _run([node, str(runner), FILTER], cwd=source, timeout=timeout, env=env)
    node_combined = (node_result.stdout or "") + (node_result.stderr or "")
    match = NODE_SUITE_RE.search(node_combined)
    passed = int(match.group(1)) if match else 0
    failed = int(match.group(2)) if match else 0
    _assertion(
        context,
        "evidence.node_suite",
        node_result.returncode == 0 and failed == 0 and passed >= MIN_TESTS,
        f"rc={node_result.returncode} passed={passed} failed={failed} min={MIN_TESTS}",
    )
    _write_text(out, NODE_LOG_NAME, _truncate(node_combined, 16000))
    _add_artifact(context, NODE_LOG_NAME)

    # ---- Row 2: ACV/feature network purity ------------------------------
    fetch_hits, hit_lines = _viz_fetch_scan(source)
    feature_source = (source / FEATURE_REL).read_text(encoding="utf-8")
    feature_fetch_hits = feature_source.count("fetch(")
    _assertion(
        context,
        "evidence.no_fetch",
        fetch_hits == 0 and feature_fetch_hits == 0,
        f"visualization fetch hits={fetch_hits} ({'; '.join(hit_lines[:5])}) "
        f"feature fetch hits={feature_fetch_hits}",
    )

    # ---- Row 3: real browser against the frozen bundle -------------------
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
            "ACP2_EVIDENCE_FRONTEND": str(source / "frontend"),
            "ACP2_EVIDENCE_OUT": str(out),
            "ACP2_EVIDENCE_SCREENSHOT": str(out / SCREENSHOT_NAME),
            "ACP2_EVIDENCE_BUNDLE": str(bundle_path),
            "ACCEPTANCE_DIR": str(Path(__file__).resolve().parent),
            "ACP2_ACCEPTANCE_DIR": str(Path(__file__).resolve().parent),
        }
    )
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
    _assertion(
        context,
        "evidence.browser.probe_completed",
        probe.returncode == 0 and bool(payload) and "error" not in payload,
        f"rc={probe.returncode} error={payload.get('error')} port={payload.get('port')}",
    )
    _assertion(
        context,
        "evidence.browser.entry_visible",
        payload.get("entry_visible") is True,
        f"entry_visible={payload.get('entry_visible')}",
    )
    _assertion(
        context,
        "evidence.browser.claim_states_distinct",
        payload.get("four_states_distinct") is True,
        f"claim_states={payload.get('claim_states')}",
    )
    _assertion(
        context,
        "evidence.browser.inconclusive_live_not_verified",
        payload.get("inconclusive_marker") is True
        and payload.get("inconclusive_not_success_colored") is True,
        f"marker={payload.get('inconclusive_marker')} "
        f"not_success_colored={payload.get('inconclusive_not_success_colored')}",
    )
    _assertion(
        context,
        "evidence.browser.execution_next_to_claim",
        payload.get("execution_next_to_claim") is True,
        f"execution_next_to_claim={payload.get('execution_next_to_claim')}",
    )
    _assertion(
        context,
        "evidence.browser.claim_expansion",
        payload.get("expand_artifacts") is True
        and payload.get("expand_validator") is True
        and payload.get("expand_method_version") is True
        and payload.get("expand_missing_validation") is True
        and payload.get("expand_decision_source") is True,
        f"artifacts={payload.get('expand_artifacts')} validator={payload.get('expand_validator')} "
        f"method={payload.get('expand_method_version')} "
        f"missing={payload.get('expand_missing_validation')} "
        f"decision={payload.get('expand_decision_source')}",
    )
    _assertion(
        context,
        "evidence.browser.energy_save_candidate_only",
        payload.get("energy_save_candidate_only") is True
        and int(payload.get("energy_save_clicks") or 0) >= 1
        and int(payload.get("submit_calls") or 0) == 0,
        f"save_only={payload.get('energy_save_candidate_only')} "
        f"clicks={payload.get('energy_save_clicks')} submit_calls={payload.get('submit_calls')}",
    )
    _assertion(
        context,
        "evidence.browser.lifecycle",
        payload.get("hide_pauses_ticks") is True
        and payload.get("switch_task_clears_claims") is True
        and payload.get("dispose_then_tick_fires_nothing") is True,
        f"hide={payload.get('hide_pauses_ticks')} "
        f"switch={payload.get('switch_task_clears_claims')} "
        f"dispose={payload.get('dispose_detail')}",
    )
    screenshot = out / SCREENSHOT_NAME
    screenshot_bytes = screenshot.stat().st_size if screenshot.is_file() else 0
    _assertion(
        context,
        "evidence.browser.screenshot_nonempty",
        screenshot_bytes > 0 and int(payload.get("screenshot_bytes") or 0) == screenshot_bytes,
        f"screenshot_bytes={screenshot_bytes} probe_bytes={payload.get('screenshot_bytes')}",
    )
    console_errors = list(payload.get("console_errors") or [])
    page_errors = list(payload.get("page_errors") or [])
    _assertion(
        context,
        "evidence.browser.zero_console_errors",
        not console_errors and not page_errors,
        f"console_errors={len(console_errors)} page_errors={len(page_errors)} "
        + _truncate(" | ".join(console_errors + page_errors), 800),
    )
    network = list(payload.get("network") or [])
    _assertion(
        context,
        "evidence.browser.network_log",
        bool(network),
        f"entries={len(network)}",
    )

    if screenshot.is_file():
        _add_artifact(context, SCREENSHOT_NAME)
    for name in (CONSOLE_LOG_NAME, NETWORK_LOG_NAME, PAGE_HTML_NAME, PANEL_SHOT_NAME):
        if (out / name).is_file():
            _add_artifact(context, name)

    summary = {
        "scenario": SCENARIO_ID,
        "mode": mode,
        "filter": FILTER,
        "bundle": str(bundle_path),
        "node": {
            "command": [node, str(runner), FILTER],
            "exit_code": node_result.returncode,
            "passed": passed,
            "failed": failed,
            "min_tests": MIN_TESTS,
        },
        "no_fetch": {
            "visualization_hits": fetch_hits,
            "feature_hits": feature_fetch_hits,
            "hit_lines": hit_lines,
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
            "experience.evidence_mode": mode,
            "experience.evidence_suite_passed": passed,
            "experience.evidence_suite_failed": failed,
            "experience.evidence_min_tests": MIN_TESTS,
            "experience.evidence_viz_fetch_hits": fetch_hits,
            "experience.evidence_feature_fetch_hits": feature_fetch_hits,
            "experience.evidence_claim_states": payload.get("claim_states"),
            "experience.evidence_inconclusive_live_not_verified": bool(
                payload.get("inconclusive_marker")
                and payload.get("inconclusive_not_success_colored")
            ),
            "experience.evidence_dispose_tick_fires_nothing": bool(
                payload.get("dispose_then_tick_fires_nothing")
            ),
            "experience.evidence_submit_calls": payload.get("submit_calls"),
            "experience.evidence_console_errors": len(console_errors) + len(page_errors),
            "experience.evidence_screenshot_bytes": screenshot_bytes,
            "experience.evidence_chromium": payload.get("chromium"),
        }
    )

    live = mode == "live"
    publish_observations(
        context,
        {
            "transport": "present" if live else "absent",
            "controller": "absent",
            "model": "absent",
            "qc": "absent",
            "lsf": "absent",
            "browser": "present" if live else "absent",
            "artifact": "present" if live and bundle_path.is_file() else "absent",
            "denial": "absent",
        },
        model_calls=0,
        model_evidence=(
            f"evidence browser {mode} row: Chromium renders the frozen bundle; "
            "no model client is invoked (0 calls)"
        ),
    )

    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    if failures:
        context.reason = "evidence_browser_assertion_failed"
        return EXIT_FAIL
    return _finish(context, "evidence_browser_assertion_failed")


def _fixture_row(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    return _run_rows(context, source / FIXTURE_REL, "fixture")


def _live_row(context: Any) -> int:
    receipt = _research_round_receipt()
    if receipt is None:
        return _not_verified(context, "upstream_not_delivered")
    bundle_path = _live_bundle(receipt)
    if bundle_path is None:
        return _not_verified(context, "upstream_not_delivered")
    out = _out_dir(context)
    _write_text(
        out, "live-upstream.txt",
        f"research_round_receipt={receipt}\nbundle={bundle_path}\n",
    )
    _add_artifact(context, "live-upstream.txt")
    return _run_rows(context, bundle_path, "live")


def scenario_evidence_browser_live(context: Any) -> int:
    mode = getattr(context.identity, "mode", None) or "fixture"
    if mode == "live":
        return _live_row(context)
    return _fixture_row(context)


register(SCENARIO_ID, scenario_evidence_browser_live)
