"""Shared real-browser leg for the M4-02 experience rows.

This module is deliberately **not** named ``acp2_experience_scenario_*``: the C
harness auto-discovers scenario bodies only, and this file is the common
machinery the two browser rows (``same-task-browser-live``,
``approval-budget-live``) share.

What is real here, and what a reader may rely on:

* a **real Controller** (``uvicorn`` + FastAPI + SQLite) on an ephemeral
  loopback port, started in ``api-key`` identity mode over an isolated identity
  database provisioned by :func:`provision_identities` (three real accounts:
  operator, reviewer, auditor; the auditor holds read-only keys);
* a **real browser**: the Playwright-bundled Chromium and the system Microsoft
  Edge (``channel="msedge"``), each with its own browser context and its own
  credential header.  The page is the committed Workbench
  (``frontend/ACP_Workbench_v2.html``) served by that Controller, and every
  request in the row is issued by the page's own modules
  (``ACPClient.transport`` / ``ACPClient.researchCommands``) — no second client
  transport is written for the acceptance;
* the row records what the browser really saw: HTTP statuses, console messages,
  page errors, screenshots, the platform TaskRef/attempt/release and the
  artifact digests read back from the platform.  A screenshot is evidence of
  what the page displayed; it is never a substitute for the platform receipt.

Every process (Controller, browser) is closed in ``finally``.  A missing
prerequisite raises :class:`BrowserPrerequisiteError` so the scenario body can
answer ``NOT_VERIFIED`` with a precise reason instead of a fabricated pass.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "IDENTITY_ACTIONS",
    "BrowserPrerequisiteError",
    "BrowserSession",
    "HttpResult",
    "PlatformController",
    "Recorder",
    "provision_identities",
    "repo_root",
]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The three browser identities of an M4-02 row: one operator that may act, one
#: reviewer that may also act, and one auditor that must never gain write.
IDENTITY_ACTIONS: dict[str, tuple[str, ...]] = {
    # An approval decision is a *control* action on the project
    # (``_decision`` -> ``authorize_job_project(action="control")``), so an
    # approver holds control; the auditor stays read-only on purpose.
    "operator": ("read", "write", "control"),
    "reviewer": ("read", "write", "control"),
    "auditor": ("read",),
}


class BrowserPrerequisiteError(RuntimeError):
    """A real prerequisite (browser, Controller, identity store) is missing."""


def repo_root() -> Path:
    return REPO_ROOT


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    payload: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def rule(self) -> str:
        """The typed rule of one refusal (never free text)."""
        payload = self.payload
        if isinstance(payload, dict):
            detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else payload
            if isinstance(detail, dict):
                params = detail.get("params")
                if isinstance(params, dict) and params.get("rule"):
                    return str(params["rule"])
                for key in ("code", "rule", "error"):
                    if detail.get(key):
                        return str(detail[key])
            if isinstance(detail, str):
                return detail
        return ""


def http_json(
    base_url: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    key: str | None = None,
    timeout: float = 60.0,
) -> HttpResult:
    """One platform call from outside the browser (setup and assertions only)."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base_url + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        status = int(exc.code)
    try:
        payload: Any = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw[:2000]
    return HttpResult(status=status, payload=payload)


def provision_identities(
    db_path: Path,
    *,
    project: str,
    actions: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, dict[str, str]]:
    """Create three real accounts and issue one key each.

    Returns ``{label: {"key": ..., "key_id": ..., "principal_id": ...}}``.
    The plaintext keys exist only in this process; they are never written to the
    evidence root.
    """
    from acp_services.workspace.identity_lifecycle import (
        IdentityLifecycle,
        IdentityLifecyclePolicy,
    )
    from acp_services.workspace.keys import ApiKeyManager

    wanted = dict(actions or IDENTITY_ACTIONS)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    key_manager = ApiKeyManager(db_path, iterations=2_000)
    lifecycle = IdentityLifecycle(
        db_path,
        policy=IdentityLifecyclePolicy(
            deployment_tier="lab", capabilities=("authentication", "isolation")
        ),
        key_manager=key_manager,
        password_params={"kdf": "pbkdf2_hmac_sha256", "iterations": 2_000, "salt_bytes": 16},
    )
    issued: dict[str, dict[str, str]] = {}
    for label, action_names in wanted.items():
        invitation = lifecycle.invite_account(
            principal_id=f"principal_m402_{label}",
            login_id=f"m402-{label}",
            roles=["member"],
        )
        account = lifecycle.accept_invitation(
            invitation.token, password=f"M402-{label}-Strong-Passw0rd!"
        )
        key = lifecycle.issue_key(
            account.account_id,
            scope="workspace",
            actions=list(action_names),
            project=project,
        )
        issued[label] = {
            "key": key.key,
            "key_id": key.key_id,
            "principal_id": f"principal_m402_{label}",
            "actions": ",".join(action_names),
        }
    return issued


class PlatformController:
    """The real Controller process the browsers talk to (api-key identity)."""

    def __init__(
        self,
        *,
        run_root: Path,
        identity_db: Path,
        log_path: Path,
        python: str | None = None,
        tenant: str = "acp-m402-browser",
    ) -> None:
        self.run_root = Path(run_root)
        self.identity_db = Path(identity_db)
        self.log_path = Path(log_path)
        self.python = python or os.environ.get("ACP2_C_PYTHON") or sys.executable
        self.tenant = tenant
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen[str] | None = None
        self._log: Any = None

    def start(self, timeout: float = 90.0) -> str:
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        env["ACP_RUN_ROOT"] = str(self.run_root)
        # The isolated Controller resolves scientific executables from the ACP
        # config only (the same rule the other M4 legs use).  A host
        # ``CONFSEARCH_*`` shell pin can point at an unrelated binary of the
        # same name, which would make a "real science" row fail for the wrong
        # reason; the pins are dropped here and never invented.
        for name in [key for key in env if key.startswith("CONFSEARCH_")]:
            env.pop(name, None)
        env["ACP_IDENTITY_MODE"] = "api-key"
        env["ACP_IDENTITY_DB"] = str(self.identity_db)
        env["ACP_IDENTITY_TENANT"] = self.tenant
        # The isolated Controller is served on a loopback port; an empty value
        # *resets* the optional reverse-proxy pin (``deployment.public_origin``
        # in the host config), which would otherwise classify the browser's
        # same-origin write as cross-site.  This is the documented override.
        env["ACP_PUBLIC_ORIGIN"] = ""
        self._log = self.log_path.open("w", encoding="utf-8")
        self._process = subprocess.Popen(
            [
                self.python,
                "-m",
                "uvicorn",
                "acp.api.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._process.poll() is not None:
                raise BrowserPrerequisiteError(
                    "the Controller exited during startup; see " + str(self.log_path)
                )
            try:
                result = http_json(self.base_url, "GET", "/api/status", timeout=5.0)
            except OSError:
                time.sleep(0.2)
                continue
            if result.status == 200:
                return self.base_url
            time.sleep(0.2)
        raise BrowserPrerequisiteError(f"no /api/status within {timeout}s")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)
            self._process = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self) -> PlatformController:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


@dataclass
class Recorder:
    """Everything the browser reported: console, page errors, HTTP responses."""

    label: str
    console: list[dict[str, Any]] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)
    requests_failed: list[dict[str, Any]] = field(default_factory=list)
    mark: int = 0

    def attach(self, page: Any) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    def _on_console(self, message: Any) -> None:
        try:
            text = str(message.text)
        except Exception:  # noqa: BLE001 - the recorder never breaks the page
            text = "<unreadable>"
        self.console.append({"type": str(message.type), "text": text[:2000]})

    def _on_error(self, error: Any) -> None:
        self.page_errors.append(str(error)[:2000])

    def _on_response(self, response: Any) -> None:
        url = str(response.url)
        if "/api/" not in url:
            return
        self.responses.append(
            {
                "status": int(response.status),
                "method": str(response.request.method),
                "url": url.split("127.0.0.1", 1)[-1],
            }
        )

    def _on_request_failed(self, request: Any) -> None:
        self.requests_failed.append(
            {
                "url": str(request.url).split("127.0.0.1", 1)[-1],
                "failure": str(request.failure or "")[:400],
            }
        )

    # -- step windows ---------------------------------------------------- #

    def window(self) -> tuple[int, int]:
        """Mark a step boundary; returns the slice for the previous window."""
        start = self.mark
        self.mark = len(self.responses)
        return start, self.mark

    def console_errors(self, *, since: int = 0) -> list[dict[str, Any]]:
        return [entry for entry in self.console[since:] if entry["type"] in {"error", "assert"}]

    def api_failures(self, *, since: int = 0) -> list[dict[str, Any]]:
        return [entry for entry in self.responses[since:] if entry["status"] >= 400]

    def to_dict(self) -> dict[str, Any]:
        statuses = sorted({entry["status"] for entry in self.responses})
        return {
            "label": self.label,
            "console": self.console,
            "console_error_count": len(self.console_errors()),
            "page_errors": self.page_errors,
            "responses": self.responses,
            "response_statuses": statuses,
            "requests_failed": self.requests_failed,
        }


class BrowserSession:
    """A Playwright browser (bundled Chromium or system Edge) with a credential."""

    def __init__(
        self,
        *,
        label: str,
        key: str,
        out_dir: Path,
        channel: str = "",
        base_url: str = "",
        headless: bool = True,
        playwright: Any = None,
    ) -> None:
        self.label = label
        self.key = key
        self.out_dir = Path(out_dir)
        self.channel = channel
        self.base_url = base_url.rstrip("/")
        self.headless = headless
        self.recorder = Recorder(label=label)
        # Playwright's sync API may only be started once per thread: a row that
        # opens two browsers shares one started instance.
        self._playwright_shared = playwright
        self._playwright: Any = None
        self._browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.version = ""

    # -- lifecycle ------------------------------------------------------- #

    def open(self) -> BrowserSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - host specific
            raise BrowserPrerequisiteError(f"playwright is not importable: {exc}") from exc
        from browser_runtime import chromium_launch_options

        if self._playwright_shared is None:
            self._playwright = sync_playwright().start()
        else:
            self._playwright = self._playwright_shared
        options = chromium_launch_options(no_sandbox=True)
        options["headless"] = self.headless
        if self.channel:
            options["channel"] = self.channel
        try:
            self._browser = self._playwright.chromium.launch(**options)
        except Exception as exc:  # noqa: BLE001 - a launch failure is a prerequisite gap
            self._close_playwright()
            raise BrowserPrerequisiteError(
                f"{self.label}: browser launch failed ({self.channel or 'chromium'}): {exc}"
            ) from exc
        self.version = str(self._browser.version)
        self.context = self._browser.new_context(
            extra_http_headers={"Authorization": f"Bearer {self.key}"},
            viewport={"width": 1440, "height": 1000},
        )
        self.page = self.context.new_page()
        self.recorder.attach(self.page)
        return self

    def close(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
        finally:
            self.context = None
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                self._browser = None
                self._close_playwright()

    def _close_playwright(self) -> None:
        # A shared instance belongs to the row, not to this session.
        if self._playwright is not None and self._playwright_shared is None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None
        self._playwright = None

    def __enter__(self) -> BrowserSession:
        return self.open()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- page helpers ---------------------------------------------------- #

    def goto_workbench(self, path: str = "/", timeout: float = 60000.0) -> str:
        assert self.page is not None
        self.page.goto(self.base_url + path, wait_until="load", timeout=timeout)
        return str(self.page.title())

    def screenshot(self, name: str) -> Path:
        assert self.page is not None
        target = self.out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(target), full_page=False)
        return target

    def evaluate(self, script: str, arg: Any = None) -> Any:
        assert self.page is not None
        return self.page.evaluate(script, arg)

    def html(self, name: str) -> Path:
        assert self.page is not None
        target = self.out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.page.content(), encoding="utf-8")
        return target

    def record(self, name: str) -> Path:
        target = self.out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.recorder.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target


#: The page-side bridge shared by both rows: it wires the *product* transport
#: module into the product command modules and exposes one JSON-safe entry.
PAGE_BRIDGE = r"""
window.__m402 = (function () {
  "use strict";
  function adapters() {
    var t = window.ACPClient.transport;
    t.init({ defaultTimeoutMs: 120000 });
    var v1 = function (suffix, opts) { return t.request("/api/v1" + suffix, opts || {}); };
    var v2 = function (suffix, opts) { return t.request("/api/v2" + suffix, opts || {}); };
    return { v1: v1, v2: v2 };
  }
  function research() {
    var a = adapters();
    window.ACPClient.researchCommands.init({ v1: a.v1, v2: a.v2 });
    return window.ACPClient.researchCommands;
  }
  function typed(err) {
    return {
      ok: false,
      code: err && err.status ? "http_" + err.status : "error",
      status: err && err.status ? err.status : null,
      message: String((err && err.message) || err).slice(0, 600),
      body: err && err.body !== undefined ? err.body : null,
    };
  }
  function options(method, body) {
    var opts = { method: method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body);
    return opts;
  }
  return {
    adapters: adapters,
    research: research,
    typed: typed,
    options: options,
    async call(method, path, body) {
      try {
        var a = adapters();
        var fn = path.indexOf("/v1/") === 0 ? a.v1 : a.v2;
        var suffix = path.replace(/^\/v[12]/, "");
        var value = await fn(suffix, options(method, body));
        return { ok: true, status: 200, value: value };
      } catch (err) { return typed(err); }
    },
    async submitJob(body) {
      try {
        var a = adapters();
        var value = await a.v1("/jobs", options("POST", body));
        return { ok: true, status: 200, value: value };
      } catch (err) { return typed(err); }
    },
    async job(id) {
      try {
        var a = adapters();
        return { ok: true, status: 200, value: await a.v1("/jobs/" + encodeURIComponent(id)) };
      } catch (err) { return typed(err); }
    },
    async researchDecide(ref, decision, expectedRevision) {
      try {
        var rc = research();
        var scope = { project_id: window.__m402_project || "" };
        var revision = expectedRevision;
        if (revision === null || revision === undefined) {
          // A requester may read its own approval; another principal may not,
          // so the caller passes the revision it knows from the request step.
          var current = await rc.fetchApproval(scope, ref);
          revision = (current.approval && current.approval.revision) || null;
        }
        var value = await rc.decide(scope, ref, {
          decision: decision,
          expectedRevision: revision
        });
        return { ok: true, status: 200, value: value };
      } catch (err) { return typed(err); }
    },
    async researchRevoke(ref, expectedRevision) {
      try {
        var rc = research();
        var scope = { project_id: window.__m402_project || "" };
        var revision = expectedRevision;
        if (revision === null || revision === undefined) {
          var current = await rc.fetchApproval(scope, ref);
          revision = (current.approval && current.approval.revision) || null;
        }
        var value = await rc.revoke(scope, ref, { expectedRevision: revision });
        return { ok: true, status: 200, value: value };
      } catch (err) { return typed(err); }
    },
    async submitWithApproval(ref, body) {
      try {
        var rc = research();
        var scope = { project_id: window.__m402_project || "" };
        var current = await rc.fetchApproval(scope, ref);
        var value = await rc.submit(scope, body, { approval: current.approval });
        return { ok: true, status: 200, value: value };
      } catch (err) { return typed(err); }
    },
    async researchFetch(ref) {
      try {
        var rc = research();
        var scope = { project_id: window.__m402_project || "" };
        var value = await rc.fetchApproval(scope, ref);
        return { ok: true, status: 200, value: value };
      } catch (err) { return typed(err); }
    }
  };
})();
"""


def install_bridge(page: Any, project_id: str) -> None:
    """Load the page-side bridge and bind the project the row works in."""
    assert page is not None
    page.evaluate("(project) => { window.__m402_project = project; }", project_id)
    page.add_script_tag(content=PAGE_BRIDGE)
    page.evaluate("() => { window.__m402.adapters(); }")
