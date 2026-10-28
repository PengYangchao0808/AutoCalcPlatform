#!/usr/bin/env python3
"""Four-combination acceptance harness (F0/S0, F1/S0, F0/S1, F1/S1).

F = the frontend/static bundle from a git revision.
S = the service implementation from a git revision.

The harness prepares detached worktrees for F0 (the frozen baseline) and F1
(the code candidate), starts S0/S1 on different ports with temporary
``ACP_RUN_ROOT`` values, and — per cell, R12 — serves the cell's F static
bundle from a combining origin that proxies ``/api/*`` to the cell's S, then:

* loads the cell's actual frontend entry HTML + referenced JS/CSS assets,
* probes the retained old API surface,
* drives one ordinary submission (the controlled no-op ``fake`` workflow)
  through the cell origin and asserts the full lifecycle (create → poll →
  detail/files/error surface → listing side effect),
* executes the actual frontend JS and its queue refresh in Chromium,
* keeps missing-argument CLI checks and the demo fake submission as smoke
  checks, separately from the seven required ordinary legacy submissions.

By default the seven ordinary CLI entries use complete input fixtures and a
controlled scientific call boundary, with completed/failed cases and explicit
scope. ``--legacy-scenarios`` instead supplies ordinary API bodies for all seven
workflows against S. Missing prerequisites remain NOT_VERIFIED. The controlled
CLI scope never claims real QC or scheduler publication.

Contracted by ``reports/architecture_remediation_20261020/harness-contract.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

if __package__:
    from .compatibility_checks import (
        FAIL,
        NOT_VERIFIED,
        PASS,
        aggregate_result,
        browser_check,
        legacy_submission_checks,
        validate_legacy_scenarios,
    )
    from .ordinary_legacy_cli import run_legacy_cli_checks
else:
    from compatibility_checks import (
        FAIL,
        NOT_VERIFIED,
        PASS,
        aggregate_result,
        browser_check,
        legacy_submission_checks,
        validate_legacy_scenarios,
    )
    from ordinary_legacy_cli import run_legacy_cli_checks

F0_REVISION = "878e3557b3b593a0c42adf842e80fe1408db395e"
CELL_NAMES = ("F0/S0", "F1/S0", "F0/S1", "F1/S1")
LEGACY_WORKFLOWS = (
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "XtbConstrainedOpt",
    "tsmode",
    "nmr",
)
SCHEMA = "acp.acceptance.harness.v1"
SUMMARY_SCHEMA = "acp.acceptance.summary.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]

_FRONTEND_ENTRY = "ACP_Workbench_v2.html"
_ASSET_PATTERN = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+)["']""")
_MAX_ASSET_PROBES = 6


class HarnessError(RuntimeError):
    """A cell fixture or execution is invalid."""


def _sha_tree(root: Path) -> str:
    if not root.exists():
        return ""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(
    argv: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wait_http(url: str, *, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = str(exc)
        time.sleep(0.5)
    raise HarnessError(f"service did not become ready at {url}: {last}")


def _get(base: str, path: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(base + path, timeout=15) as response:
            data = response.read()
            try:
                return response.status, json.loads(data)
            except ValueError:
                return response.status, data.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def validate_cells(cells: Any, *, f0_revision: str = F0_REVISION) -> list[dict[str, Any]]:
    """Validate a cell fixture and reject S1-as-S0 / TestClient-as-F0."""
    if not isinstance(cells, list) or not cells:
        raise HarnessError("fixture must contain a non-empty cells list")
    seen: set[str] = set()
    for cell in cells:
        if not isinstance(cell, dict):
            raise HarnessError("each cell must be an object")
        name = str(cell.get("cell") or "")
        if name not in CELL_NAMES:
            raise HarnessError(f"unknown cell {name!r}")
        if name in seen:
            raise HarnessError(f"duplicate cell {name!r}")
        seen.add(name)
        front, service = name.split("/")
        front_rev = str(cell.get("frontend_revision") or "")
        service_rev = str(cell.get("service_revision") or "")
        front_kind = str(cell.get("frontend_kind") or "")
        service_kind = str(cell.get("service_kind") or "")
        if front == "F0" and front_rev != f0_revision:
            raise HarnessError(f"{name}: F0 frontend must be {f0_revision}")
        if front == "F1" and front_rev == f0_revision:
            raise HarnessError(f"{name}: F1 frontend must be a different candidate revision")
        if service == "S0" and service_rev != f0_revision:
            raise HarnessError(f"{name}: S1-as-S0 is forbidden (service must be {f0_revision})")
        if service == "S1" and service_rev == f0_revision:
            raise HarnessError(f"{name}: S0-as-S1 is forbidden (service must be the candidate)")
        if front_kind != "worktree":
            raise HarnessError(f"{name}: frontend_kind must be 'worktree'")
        if service_kind != "uvicorn":
            raise HarnessError(f"{name}: service_kind must be 'uvicorn'")
        if "testclient" in json.dumps(cell).lower():
            raise HarnessError(f"{name}: TestClient-as-F0/S0 is forbidden")
    if seen != set(CELL_NAMES):
        raise HarnessError(f"fixture must define exactly {CELL_NAMES}; got {sorted(seen)}")
    return cells


def _default_fixture(f0: str, f1: str) -> list[dict[str, Any]]:
    return [
        {
            "cell": name,
            "frontend_revision": f0 if name.startswith("F0") else f1,
            "service_revision": f0 if name.endswith("S0") else f1,
            "frontend_kind": "worktree",
            "service_kind": "uvicorn",
        }
        for name in CELL_NAMES
    ]


def _probe_service(base: str) -> dict[str, Any]:
    status_code, status = _get(base, "/api/status")
    workflows_code, workflows = _get(base, "/api/workflows")
    jobs_code, jobs = _get(base, "/api/v1/jobs")
    detail_code, _ = _get(base, "/api/v1/jobs/__missing__/detail")
    availability_code, _ = _get(base, "/api/v1/jobs/__missing__/availability")
    workflow_items = (
        workflows.get("workflows")
        if isinstance(workflows, dict)
        else workflows
        if isinstance(workflows, list)
        else []
    )
    workflow_ids = [
        str(item.get("id") or item.get("name"))
        for item in (workflow_items or [])
        if isinstance(item, dict) and (item.get("id") or item.get("name"))
    ]
    if isinstance(jobs, dict):
        raw_jobs = jobs.get("jobs") or jobs.get("items") or []
    elif isinstance(jobs, list):
        raw_jobs = jobs
    else:
        raw_jobs = []
    job_items = [item for item in raw_jobs if isinstance(item, dict)]
    ok = (
        status_code == 200
        and workflows_code == 200
        and jobs_code == 200
        and detail_code in (400, 404)
        and availability_code in (400, 404)
    )
    return {
        "status_code": status_code,
        "workflows_code": workflows_code,
        "workflows_count": len(workflow_ids),
        "workflow_ids": workflow_ids,
        "jobs_code": jobs_code,
        "jobs_count": len(job_items),
        "detail_missing_code": detail_code,
        "availability_missing_code": availability_code,
        "status_keys": sorted(status.keys()) if isinstance(status, dict) else [],
        "jobs_type": type(jobs).__name__,
        "ok": ok,
    }


def _legacy_commands() -> dict[str, list[str]]:
    return {
        "Confsearch": ["run", "Confsearch"],
        "PESsearch": ["run", "PESsearch"],
        "BatchOptimize": ["run", "BatchOptimize"],
        "XtbPathSearch": ["run", "XtbPathSearch"],
        "XtbConstrainedOpt": ["run", "XtbConstrainedOpt"],
        "tsmode": ["run", "tsmode"],
        "nmr": ["run", "nmr"],
    }


def _probe_legacy(worktree: Path, run_root: Path) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(worktree / "src")
    env["ACP_RUN_ROOT"] = str(run_root)
    for key in [key for key in env if key.startswith("CONFSEARCH_")]:
        env.pop(key, None)
    rows: list[dict[str, Any]] = []
    for workflow, args in _legacy_commands().items():
        result = _run([sys.executable, "-m", "acp.cli", *args], cwd=worktree, env=env, timeout=120)
        stderr = (result.stderr or "") + (result.stdout or "")
        legacy_selected = "frozen_group" not in stderr and "worker_bootstrap" not in stderr
        rows.append(
            {
                "workflow": workflow,
                "argv": ["python", "-m", "acp.cli", *args],
                "exit_code": result.returncode,
                "legacy_entry_selected": legacy_selected,
                "stderr_tail": stderr.strip().splitlines()[-3:],
                "ok": result.returncode == 2 and legacy_selected,
            }
        )
    return rows


def _probe_demo_cli_submission(worktree: Path, run_root: Path, tag: str) -> dict[str, Any]:
    """One ordinary demo CLI submission, separate from legacy workflow proof.

    ``acp task execute --workflow fake`` runs the controlled no-op science
    backend end-to-end through the Services task CLI; ``acp run fake`` is not
    a registered CLI choice (fake is API/scheduler-only).
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(worktree / "src")
    # Dedicated run root: the cell's uvicorn service owns the shared one
    # (single-instance lock), while this leg exercises the CLI submission path.
    dedicated_root = run_root / "demo_cli"
    dedicated_root.mkdir(parents=True, exist_ok=True)
    env["ACP_RUN_ROOT"] = str(dedicated_root)
    for key in [key for key in env if key.startswith("CONFSEARCH_")]:
        env.pop(key, None)
    argv = [
        sys.executable,
        "-m",
        "acp.cli",
        "task",
        "execute",
        "--workflow",
        "fake",
        "--input",
        "CCO",
        "--name",
        f"four-combo-{tag}",
    ]
    result = _run(argv, cwd=worktree, env=env, timeout=180)
    status = ""
    work_dir = ""
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{") and '"command": "execute"' in line:
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            status = str(payload.get("status") or "")
            work_dir = str(payload.get("work_dir") or "")
            break
    artifacts = (
        sorted(p.name for p in Path(work_dir).rglob("*") if p.is_file())
        if work_dir and Path(work_dir).is_dir()
        else []
    )
    return {
        "workflow": "fake",
        "argv": ["python", "-m", "acp.cli", "task", "execute", "--workflow", "fake", "..."],
        "exit_code": result.returncode,
        "status": status,
        "work_dir": work_dir,
        "artifacts": artifacts[:20],
        "stderr_tail": (result.stderr or "").strip().splitlines()[-3:],
        "ok": result.returncode == 0 and status == "completed" and bool(artifacts),
    }


class _CellOriginServer:
    """Serve the cell's F static bundle; proxy ``/api/*`` to the cell's S.

    Mirrors the service's own static mapping (``/`` →
    ``ACP_Workbench_v2.html``, ``/js|/css`` from ``frontend/``) so the browser
    contract — same-origin static + API — holds for every F/S pairing.
    """

    def __init__(self, frontend_dir: Path, api_port: int) -> None:
        self._frontend = frontend_dir
        self._api_base = f"http://127.0.0.1:{api_port}"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _respond(
                self, status: int, payload: bytes, content_type: str | None = None
            ) -> None:
                try:
                    self.send_response(status)
                    if content_type:
                        self.send_header("Content-Type", content_type)
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    # Closing a browser cancels its outstanding reads; there
                    # is no connected client to receive a second 502 response.
                    return

            def _proxy(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                request = urllib.request.Request(
                    outer._api_base + self.path, data=body, method=method
                )
                for header in ("Content-Type", "Accept"):
                    if self.headers.get(header):
                        request.add_header(header, self.headers[header])
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        payload = response.read()
                        self._respond(
                            response.status, payload, response.headers.get("Content-Type")
                        )
                except urllib.error.HTTPError as exc:
                    payload = exc.read()
                    self._respond(exc.code, payload, exc.headers.get("Content-Type"))
                except (urllib.error.URLError, OSError) as exc:
                    self._respond(502, str(exc).encode("utf-8", "replace"))

            def _static(self) -> None:
                path = self.path.split("?", 1)[0].split("#", 1)[0]
                if path in ("/", "/index.html"):
                    target = outer._frontend / _FRONTEND_ENTRY
                else:
                    relative = path.lstrip("/")
                    target = (outer._frontend / relative).resolve()
                    if not str(target).startswith(str(outer._frontend.resolve())):
                        self.send_response(404)
                        self.end_headers()
                        return
                if not target.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                content = target.read_bytes()
                self.send_response(200)
                if target.suffix == ".html":
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                elif target.suffix == ".js":
                    self.send_header("Content-Type", "text/javascript; charset=utf-8")
                elif target.suffix == ".css":
                    self.send_header("Content-Type", "text/css; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/api/"):
                    self._proxy("GET")
                else:
                    self._static()

            def do_POST(self) -> None:  # noqa: N802
                if self.path.startswith("/api/"):
                    self._proxy("POST")
                else:
                    self.send_response(405)
                    self.end_headers()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _probe_frontend_load(base: str) -> dict[str, Any]:
    """Actually load the cell's frontend entry + referenced assets (R12)."""
    index_code, html = _get(base, "/")
    assets: list[dict[str, Any]] = []
    index_ok = index_code == 200 and isinstance(html, str) and len(html) > 0
    if isinstance(html, str):
        referenced = []
        for match in _ASSET_PATTERN.finditer(html):
            asset = match.group(1)
            if asset.startswith(("http://", "https://", "data:", "//")):
                continue
            if not asset.startswith("/"):
                asset = "/" + asset.lstrip("/")
            referenced.append(asset.split("?")[0])
        for asset in list(dict.fromkeys(referenced))[:_MAX_ASSET_PROBES]:
            code, _ = _get(base, asset)
            assets.append({"path": asset, "status": code})
    assets_ok = bool(assets) and all(entry["status"] == 200 for entry in assets)
    return {
        "index_status": index_code,
        "html_bytes": len(html) if isinstance(html, str) else 0,
        "assets": assets,
        "ok": bool(index_ok and assets_ok),
    }


def _request_json(base: str, path: str, payload: Any, *, method: str = "POST") -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def _probe_api_submission(base: str, tag: str) -> dict[str, Any]:
    """Ordinary submission through the cell origin (controlled fake backend)."""
    create_code, created = _request_json(
        base,
        "/api/jobs",
        {"workflow": "fake", "name": f"four-combo-{tag}", "input": {"source": "CCO"}},
    )
    if create_code != 201 or not isinstance(created, dict):
        return {
            "created": False,
            "create_status": create_code,
            "create_response": created
            if not isinstance(created, dict)
            else {
                "job_id": created.get("job_id"),
                "status": created.get("status"),
            },
            "ok": False,
        }
    job_id = str(created.get("job_id"))
    final_status = ""
    for _ in range(120):
        code, record = _get(base, f"/api/jobs/{job_id}")
        status = str(record.get("status")) if isinstance(record, dict) else ""
        if status in ("completed", "failed", "cancelled"):
            final_status = status
            break
        time.sleep(0.5)
    list_code, listing = _get(base, "/api/jobs")
    listed = (
        any(str(job.get("id")) == job_id for job in listing.get("jobs", []))
        if isinstance(listing, dict)
        else False
    )
    detail_code, _detail = _get(base, f"/api/v1/jobs/{job_id}/detail")
    files_code, files = _get(base, f"/api/jobs/{job_id}/files")
    file_paths = (
        {str(entry.get("path")) for entry in files.get("files", [])}
        if isinstance(files, dict)
        else set()
    )
    error_code, _error_body = _get(base, "/api/v1/jobs/__missing__/detail")
    return {
        "created": True,
        "job_id": job_id,
        "final_status": final_status,
        "detail_status": detail_code,
        "files_status": files_code,
        "has_state_json": "state.json" in file_paths,
        "listed": listed,
        "list_status": list_code,
        "missing_detail_status": error_code,
        "ok": (
            final_status == "completed"
            and detail_code == 200
            and files_code == 200
            and "state.json" in file_paths
            and listed
            and error_code in (400, 404)
        ),
    }


def _start_service(python: str, worktree: Path, port: int, run_root: Path) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(worktree / "src")
    env["ACP_RUN_ROOT"] = str(run_root)
    for key in [key for key in env if key.startswith("CONFSEARCH_")]:
        env.pop(key, None)
    return subprocess.Popen(
        [python, "-m", "uvicorn", "acp.api.server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(worktree),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def execute(
    *,
    f0: str = F0_REVISION,
    f1: str = "HEAD",
    out: Path,
    cells: list[str] | None = None,
    python: str = sys.executable,
    legacy_scenarios: dict[str, dict[str, Any]] | None = None,
    candidate_patch: Path | None = None,
) -> dict[str, Any]:
    selected = list(cells or CELL_NAMES)
    for name in selected:
        if name not in CELL_NAMES:
            raise HarnessError(f"unknown cell {name!r}")
    if legacy_scenarios is not None:
        validate_legacy_scenarios(legacy_scenarios, LEGACY_WORKFLOWS)
    patch_digest = (
        hashlib.sha256(candidate_patch.read_bytes()).hexdigest() if candidate_patch else None
    )
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="acp-four-"))
    services: list[subprocess.Popen[str]] = []
    worktrees: dict[str, Path] = {}
    try:
        for label, revision in (("F0", f0), ("F1", f1)):
            tree = root / label
            result = _run(
                ["git", "worktree", "add", "--detach", str(tree), revision],
                cwd=REPO_ROOT,
                timeout=180,
            )
            if result.returncode != 0:
                raise HarnessError(f"cannot create {label} worktree: {result.stderr[-500:]}")
            worktrees[label] = tree
        if candidate_patch is not None:
            result = _run(
                ["git", "apply", str(candidate_patch.resolve())],
                cwd=worktrees["F1"],
                timeout=30,
            )
            if result.returncode != 0:
                raise HarnessError(f"cannot apply candidate patch: {result.stderr[-500:]}")
        ports = {"S0": _free_port(), "S1": _free_port()}
        service_results: dict[str, dict[str, Any]] = {}
        legacy_results: dict[str, list[dict[str, Any]]] = {}
        legacy_real_results: dict[str, dict[str, Any]] = {}
        legacy_submission_results: dict[str, list[dict[str, Any]]] = {}
        for service in ("S0", "S1"):
            tree = worktrees["F0" if service == "S0" else "F1"]
            run_root = root / f"run_{service}"
            run_root.mkdir()
            proc = _start_service(python, tree, ports[service], run_root)
            services.append(proc)
            base = f"http://127.0.0.1:{ports[service]}"
            _wait_http(base + "/api/status")
            service_results[service] = _probe_service(base)
            legacy_results[service] = _probe_legacy(tree, run_root / "cli")
            legacy_real_results[service] = _probe_demo_cli_submission(tree, run_root, service)
            legacy_submission_results[service] = (
                legacy_submission_checks(
                    base, LEGACY_WORKFLOWS, legacy_scenarios, get=_get, post=_request_json
                )
                if legacy_scenarios is not None
                else run_legacy_cli_checks(tree, run_root / "ordinary_cli", LEGACY_WORKFLOWS)
            )
        summary: dict[str, Any] = {
            "schema": SUMMARY_SCHEMA,
            "scenarios": [],
            "all_pass": True,
        }
        for name in selected:
            front, service = name.split("/")
            tree = worktrees[front]
            # R12: every cell exercises ITS F/S pairing — the cell's static
            # bundle is served from a combining origin whose /api/* is proxied
            # to the cell's service, then really loaded and driven.
            origin = _CellOriginServer(tree / "frontend", ports[service])
            origin.start()
            try:
                frontend_result = _probe_frontend_load(origin.base)
                submission_result = _probe_api_submission(origin.base, name.replace("/", ""))
                browser_result = browser_check(
                    origin.base, expected_job_id=str(submission_result.get("job_id") or "")
                )
            finally:
                origin.stop()
            document = {
                "schema": SCHEMA,
                "harness": "four_combinations.py",
                "harness_version": "3",
                "scenario": name,
                "candidate_revision": f1,
                "candidate_patch_sha256": patch_digest,
                "f0_revision": f0,
                "f1_revision": f1,
                "environment": {
                    "python": sys.version.split()[0],
                    "repo": str(REPO_ROOT),
                    "service_port": ports[service],
                    "cell_origin_port": origin.port,
                },
                "result": "PASS",
                "scope": "IN_SCOPE",
                "skip_reason": None,
                "not_verified_reason": None,
                "steps": [
                    {
                        "id": "service_probe",
                        "command": f"GET {name} service /api/status /api/workflows /api/v1/jobs",
                        "exit_code": 0 if service_results[service]["ok"] else 1,
                        "assertions": [
                            {
                                "name": "old_surface",
                                "expected": "old surface 200; missing 400/404",
                                "actual": service_results[service],
                                "ok": service_results[service]["ok"],
                            }
                        ],
                        "ok": service_results[service]["ok"],
                    },
                    {
                        "id": "frontend_load",
                        "command": f"GET {name} cell-origin / + referenced js/css",
                        "exit_code": 0 if frontend_result["ok"] else 1,
                        "assertions": [frontend_result],
                        "ok": frontend_result["ok"],
                    },
                    {
                        "id": "api_submission",
                        "command": f"POST {name} cell-origin /api/jobs (fake), poll, detail/files",
                        "exit_code": 0 if submission_result["ok"] else 1,
                        "assertions": [submission_result],
                        "ok": submission_result["ok"],
                    },
                    {
                        "id": "legacy_cli_missing_args",
                        "command": "python -m acp.cli run <legacy> (missing-argument form)",
                        "exit_code": 0 if all(row["ok"] for row in legacy_results[service]) else 1,
                        "assertions": legacy_results[service],
                        "ok": all(row["ok"] for row in legacy_results[service]),
                    },
                    {
                        "id": "demo_cli_submission",
                        "command": "python -m acp.cli task execute --workflow fake",
                        "exit_code": 0 if legacy_real_results[service]["ok"] else 1,
                        "assertions": [legacy_real_results[service]],
                        "ok": legacy_real_results[service]["ok"],
                    },
                    {
                        "id": "frontend_browser",
                        "command": f"Chromium {name} cell-origin /; actual bootstrap + refreshJobs",
                        "exit_code": 0
                        if browser_result["result"] == PASS
                        else 2
                        if browser_result["result"] == NOT_VERIFIED
                        else 1,
                        "assertions": [browser_result],
                        "result": browser_result["result"],
                        "ok": browser_result["ok"],
                    },
                    {
                        "id": "legacy_ordinary_submissions",
                        "command": (
                            "POST /api/jobs for each legacy workflow fixture; "
                            "poll and verify dispatch events"
                        )
                        if legacy_scenarios is not None
                        else f"{python} {Path(__file__).with_name('ordinary_legacy_cli.py')} "
                        f"--worktree {worktrees['F0' if service == 'S0' else 'F1']} "
                        f"--run-root {root / f'run_{service}' / 'ordinary_cli'}",
                        "exit_code": 0
                        if aggregate_result(legacy_submission_results[service]) == PASS
                        else 2
                        if aggregate_result(legacy_submission_results[service]) == NOT_VERIFIED
                        else 1,
                        "assertions": legacy_submission_results[service],
                        "result": aggregate_result(legacy_submission_results[service]),
                        "ok": aggregate_result(legacy_submission_results[service]) == PASS,
                    },
                ],
                "artifacts": [
                    {
                        "path": f"frontend/{front}",
                        "sha256": _sha_tree(tree / "frontend"),
                        "bytes": sum(
                            p.stat().st_size for p in (tree / "frontend").rglob("*") if p.is_file()
                        )
                        if (tree / "frontend").exists()
                        else 0,
                    }
                ],
                "metrics": {
                    "requests": 0,
                    "p50_ms": None,
                    "p95_ms": None,
                    "remote_io": None,
                    "db_writes": None,
                    "block_size_bytes": 32768,
                    "cache": None,
                    "browser": None,
                },
                "cell": {
                    "frontend_revision": f0 if front == "F0" else f1,
                    "service_revision": f0 if service == "S0" else f1,
                    "frontend_sha256": _sha_tree(tree / "frontend"),
                    "service_config_sha256": _sha_tree(
                        worktrees["F0" if service == "S0" else "F1"] / "config"
                    ),
                    "service": service_results[service],
                    "frontend": frontend_result,
                    "submission": submission_result,
                    "legacy": legacy_results[service],
                    "demo_cli": legacy_real_results[service],
                    "legacy_ordinary": legacy_submission_results[service],
                    "browser": browser_result,
                },
            }
            document["result"] = aggregate_result(document["steps"])
            unverified = [
                row
                for step in document["steps"]
                for row in step["assertions"]
                if row.get("result") == NOT_VERIFIED
            ]
            if unverified:
                document["not_verified_reason"] = "; ".join(
                    f"{row.get('workflow', 'browser')}: {row.get('reason', 'not executed')}"
                    for row in unverified
                )
            summary["all_pass"] = summary["all_pass"] and document["result"] == PASS
            (out / f"{name.replace('/', '_')}.json").write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            summary["scenarios"].append(
                {
                    "scenario": name,
                    "result": document["result"],
                    "skip_reason": None,
                }
            )
        summary["result"] = aggregate_result(summary["scenarios"])
        (out / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary
    finally:
        for proc in services:
            _stop(proc)
        for tree in worktrees.values():
            _run(["git", "worktree", "remove", "--force", str(tree)], cwd=REPO_ROOT, timeout=120)
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", metavar="FIXTURE", help="validate a cell fixture only")
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing")
    parser.add_argument("--out", help="output directory")
    parser.add_argument("--f0-revision", default=F0_REVISION)
    parser.add_argument("--f1-revision", default="HEAD")
    parser.add_argument("--cells", default=",".join(CELL_NAMES))
    parser.add_argument(
        "--legacy-scenarios",
        type=Path,
        help="JSON mapping all seven legacy workflows to ordinary API body/expected_status",
    )
    parser.add_argument(
        "--candidate-patch",
        type=Path,
        help="apply a frozen source patch only to detached F1/S1; record its SHA256",
    )
    args = parser.parse_args(argv)
    cells = [part.strip() for part in str(args.cells).split(",") if part.strip()]
    if args.check_only:
        try:
            fixture = json.loads(Path(args.check_only).read_text(encoding="utf-8"))
            validate_cells(fixture.get("cells"), f0_revision=args.f0_revision)
        except (OSError, ValueError, HarnessError) as exc:
            print(f"INVALID: {exc}", file=sys.stderr)
            return 1
        print("VALID")
        return 0
    if args.dry_run:
        print(f"git worktree add --detach <tmp>/F0 {args.f0_revision}")
        print(f"git worktree add --detach <tmp>/F1 {args.f1_revision}")
        for service in ("S0", "S1"):
            print(
                f"python -m uvicorn acp.api.server:app --host 127.0.0.1 --port <{service}-port> "
                f"(ACP_RUN_ROOT=<tmp>/run_{service}, PYTHONPATH=<{service} worktree>/src)"
            )
        print(f"cells: {', '.join(cells)}")
        return 0
    if not args.out:
        print("--out is required for execution", file=sys.stderr)
        return 3
    try:
        scenarios = None
        if args.legacy_scenarios is not None:
            scenarios = validate_legacy_scenarios(
                json.loads(args.legacy_scenarios.read_text(encoding="utf-8")), LEGACY_WORKFLOWS
            )
        summary = execute(
            f0=args.f0_revision,
            f1=args.f1_revision,
            out=Path(args.out),
            cells=cells,
            legacy_scenarios=scenarios,
            candidate_patch=args.candidate_patch,
        )
    except (HarnessError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"HARNESS ERROR: {exc}", file=sys.stderr)
        return 1
    return {PASS: 0, FAIL: 1, NOT_VERIFIED: 2}[summary["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
