"""Shared isolated environment for the M4-05 third-party capability slice.

The M4-05 vertical slice must prove that a third-party capability becomes
available through the **real public path** — a wheel installed into a clean
environment, enabled by the operator CLI, discovered by a new Controller
process and executed by the plugin host — without editing any ACP/RPH/Workbench
source and without an in-test ``register``.

This module is that environment, and it deliberately does one thing per
function so an evidence reader can point at the exact command that ran:

* **build** the committed fixture distributions (``tests/fixtures/acp2/science/
  external_plugin`` and the A-23 upgrade pair) offline with the platform build
  tool, and build *mutated* variants of the same source tree for the
  fail-closed negatives (incompatible API range, unknown licence);
* **install/uninstall** a wheel into a throw-away venv with
  ``--no-deps --no-index`` (nothing comes from a package index);
* **run the operator CLI** (``python -m acp.cli science-plugin …``) in that venv
  — the same command an operator runs, not a test-only shortcut;
* **start the real Controller** (``uvicorn acp.api.server:app``) from that venv
  with an isolated run root, so discovery reads the venv's installed
  distributions and the run root's enablement ledger;
* **seed** one completed task row (the declared stub boundary: the upstream
  science run produced the native record; no QC binary, LSF job or model call is
  reached) so the real prepare/analyze commands have an artifact to read.

Nothing here imports :mod:`acp` or starts a plugin itself: every behaviour under
test is reached through a subprocess running the product's own entry points.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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
    "CAPABILITY",
    "DEFAULT_PRINCIPAL",
    "DISTRIBUTION",
    "FIXTURE_ROOT",
    "PLUGIN",
    "PRINCIPALS_ENV",
    "REPO",
    "SCHEMA",
    "SRC",
    "TRUST_ENV",
    "UPGRADE_CAPABILITY",
    "UPGRADE_DISTRIBUTION",
    "UPGRADE_PLUGIN",
    "UPGRADE_ROOT",
    "CommandResult",
    "PluginController",
    "build_fixture_wheel",
    "create_isolated_venv",
    "http_json",
    "install_wheel",
    "prepare_variant_tree",
    "run_operator_cli",
    "seed_completed_task",
    "uninstall_distribution",
]

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
FIXTURE_ROOT = REPO / "tests" / "fixtures" / "acp2" / "science" / "external_plugin"
UPGRADE_ROOT = REPO / "tests" / "fixtures" / "acp2" / "science" / "plugin_upgrade"

#: The A-22 external plugin fixture (ships ``cccp_ui_form.json``).
DISTRIBUTION = "acp2-external-science-validator"
PLUGIN = "acp2_external.native_singlepoint"
CAPABILITY = "acp2_external_native_singlepoint"
SCHEMA = "acp2.science.external.native_singlepoint"

#: The committed A-23 v1/v2 upgrade pair (same plugin/capability, 1.0.0 → 2.0.0).
UPGRADE_DISTRIBUTION = "acp2-upgrade-science-validator"
UPGRADE_PLUGIN = "acp2_upgrade.native_singlepoint"
UPGRADE_CAPABILITY = "acp2_upgrade_native_singlepoint"

#: Trust allow-list / authorization-principal environment names (M2-05).
TRUST_ENV = "ACP_SCIENCE_PLUGIN_TRUSTED_CAPABILITIES"
PRINCIPALS_ENV = "ACP_SCIENCE_PLUGIN_AUTHORIZED_PRINCIPALS"
DEFAULT_PRINCIPAL = "controller"

_WHEEL_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "build", "dist")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One recorded subprocess invocation."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def log(self) -> str:
        return self.stdout + self.stderr

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
        }


def _run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 300.0,
) -> CommandResult:
    completed = subprocess.run(
        argv,
        cwd=str(cwd or REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


# ---------------------------------------------------------------------------
# Build (offline, deterministic wheels from the committed fixtures)
# ---------------------------------------------------------------------------


def build_fixture_wheel(
    fixture_root: Path,
    outdir: Path,
    *,
    python: str | None = None,
) -> tuple[Path, CommandResult]:
    """Build one fixture wheel offline and return ``(wheel, command)``.

    Uses ``python -m build --wheel --no-isolation`` (the platform build tool,
    standard-library-only backends) and fails when it produced no wheel.  The
    caller keeps the command record as evidence of *how* the artifact was made.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    before = {path.name for path in outdir.glob("*.whl")}
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    interpreter = python or sys.executable
    result = _run(
        [
            interpreter,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(outdir),
            str(fixture_root),
        ],
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"wheel build failed: {result.log[-2000:]}")
    produced = sorted(path for path in outdir.glob("*.whl") if path.name not in before)
    if len(produced) != 1:
        raise RuntimeError(
            f"wheel build produced {[path.name for path in produced]}, expected one: "
            f"{result.log[-1500:]}"
        )
    return produced[0], result


def prepare_variant_tree(
    source: Path,
    destination: Path,
    *,
    version: str | None = None,
    license_id: str | None = None,
    api_range: str | None = None,
) -> dict[str, Any]:
    """Copy a fixture source tree and mutate its manifest for a negative case.

    Every mutation is applied to the *committed fixture copy*, never to the
    committed tree itself, and the wheel backend recomputes the distribution
    digest from the payload — so a mutated manifest can never make the declared
    digest disagree for an unrelated reason.
    """
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=_WHEEL_IGNORE)
    manifest_path = destination / "cccp_science_plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    applied: dict[str, Any] = {}
    if version is not None:
        applied["version"] = {"from": manifest.get("version"), "to": version}
        manifest["version"] = version
        meta_path = destination / "build_meta.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["version"] = version
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        backend_path = destination / "build_backend.py"
        if backend_path.is_file():
            text = backend_path.read_text(encoding="utf-8")
            text = re.sub(r'^VERSION = "[^"]*"$', f'VERSION = "{version}"', text, flags=re.M)
            backend_path.write_text(text, encoding="utf-8")
    if license_id is not None:
        applied["license"] = {"from": manifest.get("license"), "to": license_id}
        manifest["license"] = license_id
        backend_path = destination / "build_backend.py"
        if backend_path.is_file():
            text = backend_path.read_text(encoding="utf-8")
            if re.search(r'^LICENSE_ID = "[^"]*"$', text, flags=re.M):
                text = re.sub(
                    r'^LICENSE_ID = "[^"]*"$', f'LICENSE_ID = "{license_id}"', text, flags=re.M
                )
                backend_path.write_text(text, encoding="utf-8")
    if api_range is not None:
        applied["api.compatible_range"] = {
            "from": (manifest.get("api") or {}).get("compatible_range"),
            "to": api_range,
        }
        manifest.setdefault("api", {})["compatible_range"] = api_range
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return applied


# ---------------------------------------------------------------------------
# Clean environment
# ---------------------------------------------------------------------------


def create_isolated_venv(root: Path) -> tuple[Path, CommandResult]:
    """Create a fresh venv (system site packages; no network install)."""
    venv_root = root / "venv"
    result = _run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_root)],
        timeout=180.0,
    )
    if result.returncode != 0:
        raise RuntimeError(f"venv creation failed: {result.log[-2000:]}")
    python = venv_root / "bin" / "python"
    if not python.is_file():
        raise RuntimeError(f"venv interpreter missing at {python}")
    return python, result


def install_wheel(python: Path, wheel: Path) -> CommandResult:
    """Install *wheel* with ``--no-deps --no-index`` (nothing from an index)."""
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    result = _run(
        [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"wheel install failed: {result.log[-2000:]}")
    return result


def uninstall_distribution(python: Path, name: str) -> CommandResult:
    """Uninstall *name* from the isolated venv."""
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    result = _run([str(python), "-m", "pip", "uninstall", "-y", name], env=env)
    if result.returncode != 0:
        raise RuntimeError(f"uninstall failed: {result.log[-2000:]}")
    return result


def venv_env(
    *,
    run_root: Path | None = None,
    trusted: tuple[str, ...] = (),
    principals: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Environment for a venv product process (source tree on ``PYTHONPATH``)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env.pop(TRUST_ENV, None)
    env.pop(PRINCIPALS_ENV, None)
    if trusted:
        env[TRUST_ENV] = ",".join(trusted)
    if principals:
        env[PRINCIPALS_ENV] = principals
    if run_root is not None:
        env["ACP_RUN_ROOT"] = str(run_root)
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Operator CLI (the real enable/disable/list path)
# ---------------------------------------------------------------------------


def run_operator_cli(
    python: Path,
    args: list[str],
    *,
    run_root: Path,
    trusted: tuple[str, ...] = (CAPABILITY, UPGRADE_CAPABILITY),
    principals: str | None = None,
) -> tuple[CommandResult, dict[str, Any]]:
    """Run ``python -m acp.cli science-plugin <args>`` and parse its JSON answer."""
    run_root.mkdir(parents=True, exist_ok=True)
    result = _run(
        [str(python), "-m", "acp.cli", "science-plugin", *args, "--run-root", str(run_root)],
        env=venv_env(run_root=run_root, trusted=trusted, principals=principals),
        timeout=120.0,
    )
    payload: dict[str, Any] = {}
    for line in reversed(result.stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break
    return result, payload


# ---------------------------------------------------------------------------
# Real Controller process (from the isolated venv)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def http_json(
    base_url: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    key: str | None = None,
    timeout: float = 60.0,
) -> tuple[int, Any]:
    """One JSON HTTP call against the Controller."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base_url + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read().decode("utf-8")
    try:
        payload: Any = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw[:2000]
    return status, payload


@dataclass
class PluginController:
    """The real Controller served by the plugin environment.

    The interpreter is the isolated venv (so ``importlib.metadata`` sees the
    installed fixture distribution) and the run root is the enablement ledger
    the operator CLI wrote.  Nothing is injected into ``app.state``.
    """

    python: Path
    run_root: Path
    log_path: Path
    trusted: tuple[str, ...] = (CAPABILITY, UPGRADE_CAPABILITY)
    identity_db: Path | None = None
    identity_tenant: str = "acp-m405-plugin"
    port: int = field(default_factory=_free_port)
    process: subprocess.Popen[str] | None = None
    _log: Any = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 120.0) -> str:
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        extra: dict[str, str] = {
            "ACP_PUBLIC_ORIGIN": "",
            "ACP_MAX_RUNNING": "1",
            "ACP_POLL_INTERVAL": "1",
            "ACP_DEPLOYMENT_TIER": "local-single",
        }
        if self.identity_db is not None:
            self.identity_db.parent.mkdir(parents=True, exist_ok=True)
            extra.update(
                {
                    "ACP_IDENTITY_MODE": "api-key",
                    "ACP_IDENTITY_DB": str(self.identity_db),
                    "ACP_IDENTITY_TENANT": self.identity_tenant,
                }
            )
        env = venv_env(run_root=self.run_root, trusted=self.trusted, extra=extra)
        # The environment profile's CONFSEARCH_* pins point at unrelated host
        # binaries; this slice runs no QC executable at all.
        for key in [name for name in env if name.startswith("CONFSEARCH_")]:
            env.pop(key)
        self._log = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                str(self.python),
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
            cwd=str(REPO),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"the Controller exited during startup; see {self.log_path}")
            try:
                status, _payload = http_json(self.base_url, "GET", "/api/status", timeout=5.0)
            except OSError:
                time.sleep(0.2)
                continue
            if status == 200:
                return self.base_url
            time.sleep(0.2)
        raise RuntimeError(f"no /api/status within {timeout}s")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
            self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def log_tail(self, limit: int = 8000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8")[-limit:]
        except OSError:
            return ""

    def __enter__(self) -> PluginController:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Declared stub boundary: one completed task with a native result
# ---------------------------------------------------------------------------


def seed_completed_task(
    run_root: Path,
    *,
    job_id: str,
    attempt: int = 1,
    project_id: str = "uncategorized",
    relative_path: str,
    payload: bytes,
) -> dict[str, Any]:
    """Write one completed task row plus its ``RESULT`` file into *run_root*.

    This is the M2-05/M3-06 stub boundary, explicitly declared: the upstream
    science run (QC) is replaced by the fixture's fixed native record, so the
    plugin analysis consumes a **real prepared artifact** produced by the real
    prepare command without launching ORCA/xTB/LSF/model.  The row is written
    through the product's own store (schema migrations included); no fake
    manager and no in-memory job is created.
    """
    from acp_services.store.records import JobRecord, JobSpec, JobStatus
    from acp_services.store.store import JobStore

    run_root.mkdir(parents=True, exist_ok=True)
    work_dir = run_root / project_id / f"{job_id}-{attempt}"
    target = work_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    store = JobStore(run_root / "acp_jobs.db")
    store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="singlepoint", name=job_id, project_id=project_id),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=attempt,
            project_id=project_id,
        )
    )
    return {
        "job_id": job_id,
        "attempt": attempt,
        "project_id": project_id,
        "work_dir": str(work_dir),
        "relative_path": relative_path,
        "artifact_source": str(target),
        "artifact_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "stub_boundary": "upstream_science_qc_replaced_by_fixed_native_record",
    }
