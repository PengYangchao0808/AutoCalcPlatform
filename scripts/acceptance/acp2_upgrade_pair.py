#!/usr/bin/env python3.11
"""M4-06a upgrade-pair drill: previous release artifact -> current candidate.

This acceptance harness builds (or reuses) the two release artifact sets, installs
each one into its own venv, and drives a real upgrade/rollback drill against
isolated run roots — never the production run root:

1. **Upgrade pair.** ``previous`` = the frozen remediation baseline tag
   (``acp2-baseline-20261026``) built from a temporary git worktree;
   ``candidate`` = the current candidate tree.  Both are produced by
   ``scripts/build_release.py``, and every ``release-manifest.json`` digest is
   re-verified against the bytes on disk.
2. **Previous Controller.** A real Controller from the previous wheels writes
   real data: jobs submitted over HTTP (old local scope + request-digest v1),
   one genuine pre-M0-05 MCP submit (no idempotency key), plus the legacy state
   written by the previous artifact's own Services writers through
   ``acp2_upgrade_pair_helper.py seed`` (approval, frozen ``auto_`` row,
   pending/unattributable claims, prepare-v1 operation, Proposal/ResearchRecord,
   historical usage receipt, frozen capability descriptor snapshot).
3. **Backup, upgrade, verify.** Stop, copy the whole run root to a real backup,
   start the candidate Controller on the same data, and assert: schema heads are
   migrated, old tasks/ResearchRecord/snapshot stay readable, the M0 frozen
   legacy semantics hold (replay only when provable, reconcile otherwise,
   nothing deleted), historical usage is not lost, and new writes use v2
   identity/digest (never ``auto_``/``op_``).
4. **Rollback.** Stop, restore the backup, restart the previous Controller and
   prove the data is readable at the previous schema with no half-migrated
   state.
5. **Failure injection.** SIGKILL the candidate migration before ``024`` on a
   copy, prove the jobs DB is readable with every job intact (no bare delete,
   no half-applied migration row), then migrate the same file to the head.

Evidence is written under ``--out`` with ``evidence_kind=fixture+aggregate`` and
an explicit ``not_production_data`` marker.  Exit code ``0`` only when every
assertion passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

EXIT_PASS = 0
EXIT_FAIL = 1

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = Path(__file__).resolve().parent / "acp2_upgrade_pair_helper.py"
BUILD_RELEASE = REPO_ROOT / "scripts" / "build_release.py"

SCHEMA = "acp2.upgrade-pair.v1"
EVIDENCE_KIND = "fixture+aggregate"
PREVIOUS_REF = "acp2-baseline-20261026"
DEFAULT_ARTIFACT_ROOT = Path("/ACPServer/acp2-artifacts/remediation/m4-06a")
DEFAULT_WORK_DIR = Path("/tmp/opencode/acp2-m4-06a-work")

#: Isolation guard: this harness must never touch the production run root.
FORBIDDEN_RUN_ROOTS = (
    Path("/ACPServer/acp2-runs/remediation"),
    Path("/ACPServer/acp2-runs"),
)

#: Tables compared across the upgrade (jobs DB / admission DB / research DB).
JOBS_TABLES = (
    "jobs",
    "tasks",
    "stage_tasks",
    "artifacts",
    "job_outbox",
    "approval_records",
    "idempotency_records",
    "structure_source_index",
    "organization_events",
    "data_operations",
)
ADMISSION_TABLES = (
    "admission_reservations",
    "admission_usage_receipts",
    "research_budget_policies",
    "research_budget_reservations",
    "research_budget_receipts",
    "research_budget_events",
)
RESEARCH_TABLES = ("research_resources",)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digests(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _table_state(db: Path, tables: Sequence[str]) -> dict[str, Any]:
    if not db.is_file():
        return {"present": False, "counts": {}, "digests": {}}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        existing = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        counts: dict[str, int] = {}
        digests: dict[str, str] = {}
        for table in tables:
            if table not in existing:
                continue
            rows = conn.execute(f"SELECT * FROM [{table}] ORDER BY rowid").fetchall()
            counts[table] = len(rows)
            digests[table] = hashlib.sha256(
                json.dumps([dict(row) for row in rows], sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
        return {"present": True, "counts": counts, "digests": digests}
    finally:
        conn.close()


def _schema_versions(run_root: Path) -> dict[str, Any]:
    jobs_db = run_root / "acp_jobs.db"
    admission_db = run_root / "acp_admission.db"
    payload: dict[str, Any] = {
        "jobs": {"present": jobs_db.is_file(), "count": 0, "ids": []},
        "prepare": 0,
        "admission": 0,
    }
    if jobs_db.is_file():
        conn = sqlite3.connect(str(jobs_db))
        try:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "_schema_migrations" in tables:
                payload["jobs"]["count"] = int(
                    conn.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
                )
                payload["jobs"]["ids"] = [
                    str(row[0])
                    for row in conn.execute("SELECT id FROM _schema_migrations ORDER BY id")
                ]
            if "_prepare_schema_migrations" in tables:
                row = conn.execute("SELECT MAX(id) FROM _prepare_schema_migrations").fetchone()
                payload["prepare"] = 0 if row is None or row[0] is None else int(row[0])
            payload["jobs"]["integrity"] = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
    if admission_db.is_file():
        conn = sqlite3.connect(str(admission_db))
        try:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "admission_schema_migrations" in tables:
                row = conn.execute("SELECT MAX(id) FROM admission_schema_migrations").fetchone()
                payload["admission"] = 0 if row is None or row[0] is None else int(row[0])
            payload["admission_integrity"] = str(
                conn.execute("PRAGMA integrity_check").fetchone()[0]
            )
        finally:
            conn.close()
    return payload


def _forbidden_production_root(run_root: Path) -> Path | None:
    """Return the production run root this drill must never write into."""
    for forbidden in FORBIDDEN_RUN_ROOTS:
        if run_root == forbidden or forbidden in run_root.parents:
            return forbidden
    return None


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _clean_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if extra:
        env.update(extra)
    return env


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
    log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(item) for item in argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(env),
        check=False,
    )
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "$ " + " ".join(str(item) for item in argv) + "\n"
            f"exit={completed.returncode}\n"
            f"--- stdout ---\n{completed.stdout}\n"
            f"--- stderr ---\n{completed.stderr}\n",
            encoding="utf-8",
        )
    return completed


def _http_json(
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 20.0,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method="POST" if data else "GET"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    document = json.loads(body) if body.strip() else {}
    if not isinstance(document, dict):
        raise ValueError(f"non-object response from {url}")
    return document


def _http_status(url: str, *, timeout: float = 20.0) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except urllib.error.URLError as exc:  # pragma: no cover - transport
        raise RuntimeError(f"controller unreachable: {exc}") from exc


TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _cancel_and_settle(
    base: str, job_ids: Sequence[str], *, timeout: float = 180.0
) -> dict[str, str]:
    """Cancel every just-submitted legacy task and wait for a terminal state.

    The drill needs a quiescent run root before it snapshots and copies it: a
    still-running local task would keep writing QC scratch files and the backup
    would no longer be a consistent point-in-time copy.
    """
    settled: dict[str, str] = {}
    for job_id in job_ids:
        if not job_id:
            continue
        try:
            _http_json(f"{base}/api/v1/jobs/{job_id}/cancel", payload={})
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
            pass
    deadline = time.monotonic() + timeout
    pending = [job_id for job_id in job_ids if job_id]
    while pending and time.monotonic() < deadline:
        remaining: list[str] = []
        for job_id in pending:
            try:
                record = _http_json(f"{base}/api/v1/jobs/{job_id}")
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
                remaining.append(job_id)
                continue
            status = str(record.get("status") or "")
            if status in TERMINAL_JOB_STATUSES:
                settled[job_id] = status
            else:
                remaining.append(job_id)
        pending = remaining
        if pending:
            time.sleep(0.5)
    for job_id in pending:
        settled[job_id] = "pending"
    return settled


def _venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


class Assertions:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: Any = "") -> None:
        rendered = (
            detail if isinstance(detail, str) else json.dumps(detail, sort_keys=True, default=str)
        )
        self.rows.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": rendered})

    def summary(self) -> dict[str, Any]:
        failing = [row["name"] for row in self.rows if row["status"] != "PASS"]
        return {
            "total": len(self.rows),
            "passed": len(self.rows) - len(failing),
            "failed": len(failing),
            "failing": failing,
            "status": "PASS" if self.rows and not failing else "FAIL",
        }


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _ensure_build_tool(artifact_root: Path, log_dir: Path) -> Path:
    """A python that can run ``setuptools.build_meta.build_wheel``."""
    probe = subprocess.run(
        [sys.executable, "-c", "import setuptools, wheel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        return Path(sys.executable)
    tool_venv = artifact_root / "tooling-build-venv"
    python = _venv_python(tool_venv)
    if not python.is_file():
        _run([sys.executable, "-m", "venv", str(tool_venv)], cwd=artifact_root, timeout=300)
        installed = _run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "-q",
                "setuptools>=65",
                "wheel",
            ],
            cwd=artifact_root,
            timeout=900,
            log=log_dir / "tooling-install.log",
        )
        if installed.returncode != 0:
            raise RuntimeError("could not provision the build toolchain")
    return python


def _worktree(ref: str, directory: Path) -> Path:
    """Check out *ref* in *directory* (reusing an existing clean worktree)."""
    if (directory / ".git").exists():
        head = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        expected = _git("rev-parse", f"{ref}^{{commit}}")
        if head != expected:
            raise RuntimeError(f"worktree {directory} is at {head}, expected {expected}")
        return directory
    directory.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "--detach", str(directory), ref)
    return directory


def _remove_worktree(directory: Path) -> None:
    subprocess.run(
        ["git", "-C", str(REPO_ROOT), "worktree", "remove", "--force", str(directory)],
        capture_output=True,
        text=True,
        check=False,
    )


def _verify_release_manifest(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "release-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"missing release manifest in {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failing: list[str] = []
    for name, expected in (manifest.get("artifacts") or {}).items():
        path = directory / name
        if not path.is_file():
            failing.append(f"{name}: missing")
            continue
        actual = _sha256(path)
        if actual != expected:
            failing.append(f"{name}: {actual} != {expected}")
    roles = sorted((manifest.get("roles") or {}).keys())
    return {
        "path": str(manifest_path),
        "schema": manifest.get("schema"),
        "version": manifest.get("version"),
        "source": manifest.get("source"),
        "roles": roles,
        "artifacts": manifest.get("artifacts"),
        "digest_failing": failing,
        "digest_ok": not failing,
        "manifest_sha256": _sha256(manifest_path),
    }


def _build_artifact(
    *,
    source: Path,
    out: Path,
    build_python: Path,
    log: Path,
    allow_dirty: bool,
) -> dict[str, Any]:
    if out.exists() and any(out.iterdir()):
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    argv = [build_python, BUILD_RELEASE, "--source", str(source), "--out", str(out)]
    if allow_dirty:
        argv.append("--allow-dirty")
    completed = _run(argv, cwd=source, timeout=1800, log=log)
    if completed.returncode != 0:
        raise RuntimeError(f"release build failed (see {log})")
    return _verify_release_manifest(out)


def _install_venv(
    *,
    artifact_dir: Path,
    venv: Path,
    log_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    python = _venv_python(venv)
    wheels = sorted(path.name for path in artifact_dir.glob("*.whl"))
    expected = sorted(["cccp-", "acp_worker-", "acp_web-"])
    for prefix in expected:
        if not any(name.startswith(prefix) for name in wheels):
            raise RuntimeError(f"artifact set {artifact_dir} has no {prefix}* wheel")
    marker_payload = {
        "wheels": {name: _sha256(artifact_dir / name) for name in wheels},
        "python": str(python),
    }
    marker = venv / ".m4-06a-install.json"
    if marker.is_file() and python.is_file():
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            recorded = {}
        if recorded.get("wheels") == marker_payload["wheels"]:
            probe = _run(
                [python, "-c", "import cccp, acp, acp_services; print(cccp.__version__)"],
                cwd=venv.parent,
                timeout=120,
            )
            if probe.returncode == 0:
                return {"venv": str(venv), "reused": True, "wheels": wheels}
    if venv.exists():
        shutil.rmtree(venv)
    created = _run([sys.executable, "-m", "venv", str(venv)], cwd=venv.parent, timeout=300)
    if created.returncode != 0:
        raise RuntimeError("could not create the harness venv")
    argv = [
        python,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--find-links",
        str(artifact_dir),
        *[str(artifact_dir / name) for name in wheels],
    ]
    installed = _run(
        argv, cwd=venv.parent, timeout=timeout, log=log_dir / f"install-{venv.name}.log"
    )
    if installed.returncode != 0:
        raise RuntimeError(f"pip install failed for {venv} (see install log)")
    marker.write_text(json.dumps(marker_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"venv": str(venv), "reused": False, "wheels": wheels}


def _quiesce(run_root: Path) -> None:
    """Checkpoint every SQLite file so a file copy and its digests are stable."""
    for path in sorted(run_root.glob("*.db")):
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Controller lifecycle
# ---------------------------------------------------------------------------


class Controller:
    def __init__(
        self,
        *,
        venv: Path,
        run_root: Path,
        cwd: Path,
        log: Path,
        side: str,
    ) -> None:
        self.venv = venv
        self.run_root = run_root
        self.cwd = cwd
        self.log = log
        self.side = side
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[Any] | None = None
        self.health: dict[str, Any] = {}

    def start(self, timeout: float = 120.0) -> None:
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        stream = self.log.open("w", encoding="utf-8")
        env = _clean_env(
            {
                "ACP_RUN_ROOT": str(self.run_root),
                "ACP_HOST": "127.0.0.1",
                "ACP_PORT": str(self.port),
                "ACP_POLL_INTERVAL": "1",
                "ACP_MAX_RUNNING": "1",
                "ACP_CANDIDATE_REVISION": f"m4-06a-{self.side}",
            }
        )
        self.process = subprocess.Popen(
            [
                str(_venv_python(self.venv)),
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
            cwd=str(self.cwd),
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        self.stream = stream
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.side} Controller exited during startup (see {self.log})")
            try:
                health = _http_json(self.base + "/api/health", timeout=2)
                if health.get("status") == "ok":
                    self.health = health
                    return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"{self.side} Controller did not become healthy (see {self.log})")

    def _processes_using_venv(self) -> list[int]:
        completed = subprocess.run(
            ["pgrep", "-f", str(_venv_python(self.venv))],
            capture_output=True,
            text=True,
            check=False,
        )
        return [int(pid) for pid in completed.stdout.split() if int(pid) != os.getpid()]

    def wait_fully_stopped(self, timeout: float = 30.0) -> list[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            leftover = self._processes_using_venv()
            if not leftover:
                return []
            time.sleep(0.2)
        return self._processes_using_venv()

    def stop(self, timeout: float = 30.0) -> int | None:
        if self.process is None:
            return None
        process = self.process
        self.process = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout)
        try:
            self.stream.close()
        except OSError:  # pragma: no cover - already closed
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) != 0:
                    return process.returncode
            time.sleep(0.1)
        raise RuntimeError(f"{self.side} Controller port {self.port} stayed open")


# ---------------------------------------------------------------------------
# MCP probes (newline-delimited JSON-RPC, the frozen stdio framing)
# ---------------------------------------------------------------------------


def _mcp_submit(
    *,
    venv: Path,
    base: str,
    arguments: Mapping[str, Any],
    timeout: float = 60.0,
) -> dict[str, Any]:
    process = subprocess.Popen(
        [str(venv / "bin" / "acp"), "mcp", "serve", "--controller", base, "--log-level", "WARNING"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_clean_env(),
    )
    try:

        def exchange(payload: Mapping[str, Any], *, expect: bool = True) -> dict[str, Any]:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
            if not expect:
                return {}
            line = process.stdout.readline()
            return json.loads(line) if line.strip() else {}

        initialized = exchange(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "m4-06a", "version": "1"},
                },
            }
        )
        exchange({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect=False)
        called = exchange(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "acp_submit", "arguments": dict(arguments)},
            },
            expect=True,
        )
        return {"initialize": initialized, "call": called}
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:  # pragma: no cover
                pass
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
            process.wait(timeout=10)


def _mcp_error_code(response: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    result = response.get("call", {}).get("result") or {}
    structured = result.get("structuredContent") or {}
    error = structured.get("error") or {}
    return str(error.get("code") or ""), dict(error.get("params") or {})


def _mcp_job_reference(response: Mapping[str, Any]) -> dict[str, Any]:
    result = response.get("call", {}).get("result") or {}
    data = (result.get("structuredContent") or {}).get("data") or {}
    reference = data.get("reference") or {}
    return {
        "job_id": str(reference.get("id") or ""),
        "idempotency_key": data.get("idempotency_key"),
        "idempotent_replay": data.get("idempotent_replay"),
    }


# ---------------------------------------------------------------------------
# Drill phases
# ---------------------------------------------------------------------------


def _helper(
    *,
    venv: Path,
    action: str,
    arguments: Sequence[str],
    cwd: Path,
    timeout: float,
    log: Path,
) -> tuple[int, dict[str, Any], str]:
    completed = _run(
        [_venv_python(venv), HELPER, action, *arguments],
        cwd=cwd,
        timeout=timeout,
        log=log,
    )
    payload: dict[str, Any] = {}
    for line in reversed((completed.stdout or "").strip().splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break
    if not payload and (completed.stdout or "").strip():
        try:
            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}
    return completed.returncode, payload, (completed.stderr or "")


def _remove_tree(path: Path, attempts: int = 10) -> None:
    """Remove a tree, tolerating a just-stopped process still closing files."""
    for attempt in range(attempts):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


def _copy_tree(source: Path, target: Path) -> None:
    last: Exception | None = None
    for _ in range(10):
        _remove_tree(target)
        try:
            shutil.copytree(source, target, symlinks=True)
            return
        except (shutil.Error, OSError) as exc:  # a just-stopped process may still write
            last = exc
            time.sleep(0.3)
    raise RuntimeError(f"could not copy {source} -> {target}: {last}")


def _run_drill(args: argparse.Namespace) -> int:
    assertions = Assertions()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_dir = out / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    artifact_root = Path(args.artifact_root).resolve()
    work_dir = Path(args.work_dir).resolve()
    run_root = work_dir / "run_root"

    forbidden = _forbidden_production_root(run_root)
    if forbidden is not None:
        return _abort(
            out,
            assertions,
            f"work dir {work_dir} would write into the production run root {forbidden}",
        )
    work_dir.mkdir(parents=True, exist_ok=True)

    # -- 1. build or reuse the upgrade pair --------------------------------
    build_python = _ensure_build_tool(artifact_root, log_dir)
    pair: dict[str, Any] = {"schema": SCHEMA, "previous_ref": PREVIOUS_REF, "artifacts": {}}
    previous_wt = work_dir / "src-previous"
    candidate_wt = work_dir / "src-candidate"
    created_worktrees: list[Path] = []
    try:
        previous_artifact = artifact_root / "previous"
        candidate_artifact = artifact_root / "candidate"
        reuse = bool(args.reuse_artifacts)
        if not reuse or not (previous_artifact / "release-manifest.json").is_file():
            if not previous_wt.exists():
                created_worktrees.append(previous_wt)
            source = _worktree(args.previous_ref, previous_wt)
            previous_manifest = _build_artifact(
                source=source,
                out=previous_artifact,
                build_python=build_python,
                log=log_dir / "build-previous.log",
                allow_dirty=False,
            )
        else:
            previous_manifest = _verify_release_manifest(previous_artifact)
        pair["artifacts"]["previous"] = previous_manifest

        candidate_source = Path(args.candidate_source).resolve() if args.candidate_source else None
        pinned_candidate = artifact_root / "candidate-src"
        if candidate_source is None and (pinned_candidate / ".git").exists():
            # The artifact store may pin the exact candidate source snapshot the
            # release set was built from (HEAD + the in-scope M4-06a fix).  Its
            # manifest records revision/tree/dirty paths either way.
            candidate_source = pinned_candidate
        if candidate_source is None:
            if not candidate_wt.exists():
                created_worktrees.append(candidate_wt)
            candidate_source = _worktree("HEAD", candidate_wt)
        dirty = subprocess.run(
            ["git", "-C", str(candidate_source), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not reuse or not (candidate_artifact / "release-manifest.json").is_file():
            candidate_manifest = _build_artifact(
                source=candidate_source,
                out=candidate_artifact,
                build_python=build_python,
                log=log_dir / "build-candidate.log",
                allow_dirty=bool(dirty),
            )
        else:
            candidate_manifest = _verify_release_manifest(candidate_artifact)
        pair["artifacts"]["candidate"] = candidate_manifest
        pair["candidate_source"] = str(candidate_source)
        pair["candidate_dirty_paths"] = dirty.splitlines()
    finally:
        if not args.keep_worktrees:
            for directory in created_worktrees:
                if directory.exists() and not str(directory).startswith(str(artifact_root)):
                    _remove_worktree(directory)

    for side in ("previous", "candidate"):
        manifest = pair["artifacts"][side]
        assertions.add(
            f"pair.{side}.manifest_digests_match",
            bool(manifest["digest_ok"]) and manifest["schema"] == "acp2.release.manifest.v1",
            {"schema": manifest["schema"], "failing": manifest["digest_failing"]},
        )
    previous_revision = (pair["artifacts"]["previous"]["source"] or {}).get("revision")
    candidate_revision = (pair["artifacts"]["candidate"]["source"] or {}).get("revision")
    assertions.add(
        "pair.previous_is_baseline_tag",
        previous_revision == _git("rev-parse", PREVIOUS_REF),
        {"previous": previous_revision, "tag": _git("rev-parse", PREVIOUS_REF)},
    )
    assertions.add(
        "pair.candidate_is_current_head",
        candidate_revision == _git("rev-parse", "HEAD"),
        {"candidate": candidate_revision, "head": _git("rev-parse", "HEAD")},
    )

    # -- 2. two installed environments ------------------------------------
    venv_root = artifact_root / "venvs"
    venv_root.mkdir(parents=True, exist_ok=True)
    previous_venv = venv_root / "previous"
    candidate_venv = venv_root / "candidate"
    installs = {
        "previous": _install_venv(
            artifact_dir=artifact_root / "previous",
            venv=previous_venv,
            log_dir=log_dir,
            timeout=1800,
        ),
        "candidate": _install_venv(
            artifact_dir=artifact_root / "candidate",
            venv=candidate_venv,
            log_dir=log_dir,
            timeout=1800,
        ),
    }
    pair["installs"] = installs
    probes = {}
    for side, venv in (("previous", previous_venv), ("candidate", candidate_venv)):
        code, report, stderr = _helper(
            venv=venv,
            action="report",
            arguments=["--side", side, "--jobs-db", "", "--admission-db", ""],
            cwd=work_dir,
            timeout=300,
            log=log_dir / f"report-{side}.log",
        )
        probes[side] = report
        installed_path = str(report.get("modules", {}).get("acp", ""))
        assertions.add(
            f"install.{side}.imports_resolve_inside_venv",
            code == 0
            and installed_path.startswith(str(venv))
            and report.get("versions", {}).get("dist.cccp") is not None
            and str(stderr).strip() == "",
            {"exit": code, "acp_module": installed_path, "venv": str(venv)},
        )
    pair["probes"] = probes
    for side in ("previous", "candidate"):
        manifest = artifact_root / side / "release-manifest.json"
        if manifest.is_file():
            shutil.copy2(manifest, out / f"{side}-release-manifest.json")
    _write_json(out / "upgrade-pair.json", pair)

    previous_head = int(probes["previous"].get("jobs_schema_version") or 0)
    candidate_head = int(probes["candidate"].get("jobs_schema_version") or 0)
    candidate_prepare = int(probes["candidate"].get("prepare_schema_version") or 0)
    candidate_admission = int(probes["candidate"].get("admission_schema_version") or 0)
    assertions.add(
        "pair.candidate_is_a_real_schema_bump",
        candidate_head > previous_head and candidate_prepare >= 2 and candidate_admission >= 26,
        {
            "previous_head": previous_head,
            "candidate_head": candidate_head,
            "prepare": candidate_prepare,
            "admission": candidate_admission,
        },
    )

    # -- 3. previous Controller writes real pre-upgrade data --------------
    _remove_tree(run_root)
    run_root.mkdir(parents=True)
    previous = Controller(
        venv=previous_venv,
        run_root=run_root,
        cwd=work_dir,
        log=log_dir / "controller-previous.log",
        side="previous",
    )
    previous.start()
    legacy_jobs: dict[str, Any] = {}
    try:
        created = _http_json(
            previous.base + "/api/v1/jobs",
            payload={
                "workflow": "singlepoint",
                "name": "legacy_http_keyed",
                "input": {"source": "O"},
                "idempotency_key": "legacy-http-key",
            },
        )
        legacy_jobs["http_keyed"] = {
            "job_id": created.get("job_id"),
            "idempotency_key": created.get("idempotency_key"),
            "idempotent_replay": created.get("idempotent_replay"),
        }
        keyless = _http_json(
            previous.base + "/api/v1/jobs",
            payload={
                "workflow": "singlepoint",
                "name": "legacy_http_keyless",
                "input": {"source": "N"},
            },
        )
        legacy_jobs["http_keyless"] = {"job_id": keyless.get("job_id")}
        mcp_previous = _mcp_submit(
            venv=previous_venv,
            base=previous.base,
            arguments={
                "workflow": "singlepoint",
                "input": {"source": "C"},
                "project_id": "uncategorized",
            },
        )
        legacy_jobs["mcp_keyless"] = _mcp_job_reference(mcp_previous)
        settled = _cancel_and_settle(
            previous.base, [item.get("job_id", "") for item in legacy_jobs.values()]
        )
        assertions.add(
            "previous.real_entries_write_tasks",
            all(str(item.get("job_id") or "").startswith("20") for item in legacy_jobs.values()),
            legacy_jobs,
        )
        assertions.add(
            "previous.legacy_tasks_quiesced_before_backup",
            bool(settled) and all(status in TERMINAL_JOB_STATUSES for status in settled.values()),
            settled,
        )
    finally:
        previous.stop()
    previous_leftover = previous.wait_fully_stopped(timeout=120.0)
    assertions.add(
        "previous.controller_stopped_cleanly",
        previous.process is None and not previous_leftover,
        {"port": previous.port, "leftover_pids": previous_leftover},
    )

    code, seed, stderr = _helper(
        venv=previous_venv,
        action="seed",
        arguments=[
            "--side",
            "previous",
            "--run-root",
            str(run_root),
            "--jobs-db",
            str(run_root / "acp_jobs.db"),
            "--admission-db",
            str(run_root / "acp_admission.db"),
            "--out",
            str(out / "seed.json"),
        ],
        cwd=work_dir,
        timeout=600,
        log=log_dir / "seed-previous.log",
    )
    assertions.add(
        "previous.legacy_state_written",
        code == 0 and bool(seed.get("claims")) and bool(seed.get("prepare")),
        {"exit": code, "stderr": stderr[-500:]},
    )

    pre_upgrade = {
        "run_root": str(run_root),
        "state": _schema_versions(run_root),
        "jobs_db": _table_state(run_root / "acp_jobs.db", JOBS_TABLES),
        "admission_db": _table_state(run_root / "acp_admission.db", ADMISSION_TABLES),
        "research_db": _table_state(run_root / "acp_research.db", RESEARCH_TABLES),
        "seed": seed,
        "legacy_jobs": legacy_jobs,
        "settled": settled,
    }
    _quiesce(run_root)
    pre_upgrade["tree"] = _tree_digests(run_root)
    _write_json(out / "previous-state.json", pre_upgrade)
    assertions.add(
        "previous.schema_is_the_old_head",
        pre_upgrade["state"]["jobs"]["count"] == previous_head
        and pre_upgrade["state"]["prepare"] == 1
        and pre_upgrade["state"]["admission"] == 0,
        pre_upgrade["state"],
    )
    real_mcp_row = None
    conn = sqlite3.connect(str(run_root / "acp_jobs.db"))
    try:
        real_mcp_row = conn.execute(
            "SELECT scope_id, idempotency_key, request_digest, state, task_id "
            "FROM idempotency_records WHERE task_id=?",
            (legacy_jobs["mcp_keyless"]["job_id"],),
        ).fetchone()
    finally:
        conn.close()
    pre_upgrade["pre_m0_05_mcp_row"] = (
        None
        if real_mcp_row is None
        else {
            "scope_id": real_mcp_row[0],
            "ledger_key": real_mcp_row[1],
            "ledger_key_prefix": str(real_mcp_row[1])[:3],
            "request_digest_prefix": str(real_mcp_row[2])[:16],
            "state": real_mcp_row[3],
            "task_id": real_mcp_row[4],
            "note": "pre-M0-05 MCP scoped the auto_ derivation into an op_ ledger key",
        }
    )
    _write_json(out / "previous-state.json", pre_upgrade)
    assertions.add(
        "previous.pre_m0_05_mcp_row_observed",
        real_mcp_row is not None
        and str(real_mcp_row[1]).startswith("op_")
        and str(real_mcp_row[2]).startswith("sha256:"),
        {
            "scope_id": None if real_mcp_row is None else real_mcp_row[0],
            "ledger_key": None if real_mcp_row is None else real_mcp_row[1],
            "ledger_key_prefix": None if real_mcp_row is None else str(real_mcp_row[1])[:3],
            "note": "pre-M0-05 MCP scoped the auto_ derivation into an op_ ledger key",
        },
    )

    # -- 4. real backup, then candidate Controller on the same data -------
    backup = work_dir / "backup" / "run_root"
    _copy_tree(run_root, backup)
    _quiesce(backup)
    backup_digests = _tree_digests(backup)
    source_digests = pre_upgrade["tree"]
    differing = sorted(
        set(source_digests) ^ set(backup_digests)
        | {
            key
            for key in set(source_digests) & set(backup_digests)
            if source_digests[key] != backup_digests[key]
        }
    )
    assertions.add(
        "backup.file_copy_matches_source",
        not differing and len(backup_digests) > 0,
        {"files": len(backup_digests), "differing": differing[:20]},
    )
    _write_json(
        out / "backup-manifest.json",
        {
            "schema": "acp2.upgrade-pair.backup.v1",
            "source_run_root": str(run_root),
            "backup_root": str(backup),
            "files": len(backup_digests),
            "sha256": backup_digests,
            "pre_upgrade_state": pre_upgrade["state"],
        },
    )

    candidate = Controller(
        venv=candidate_venv,
        run_root=run_root,
        cwd=work_dir,
        log=log_dir / "controller-candidate.log",
        side="candidate",
    )
    candidate.start()
    upgrade_trace: dict[str, Any] = {}
    try:
        upgraded_state = _schema_versions(run_root)
        upgrade_trace["health"] = candidate.health
        upgrade_trace["schema"] = upgraded_state
        assertions.add(
            "upgrade.schema_migrated_to_candidate_head",
            upgraded_state["jobs"]["count"] == candidate_head
            and upgraded_state["prepare"] == candidate_prepare
            and upgraded_state["admission"] == candidate_admission
            and upgraded_state["jobs"]["integrity"] == "ok"
            and upgraded_state.get("admission_integrity") == "ok",
            upgraded_state,
        )
        assertions.add(
            "upgrade.old_migration_rows_are_a_prefix",
            upgraded_state["jobs"]["ids"][:previous_head] == pre_upgrade["state"]["jobs"]["ids"],
            {
                "previous": pre_upgrade["state"]["jobs"]["ids"],
                "candidate": upgraded_state["jobs"]["ids"],
            },
        )

        # Old tasks stay readable through the real HTTP read path.
        reads: dict[str, Any] = {}
        for label, item in legacy_jobs.items():
            job_id = item.get("job_id") or ""
            record = _http_json(f"{candidate.base}/api/v1/jobs/{job_id}")
            reads[label] = {
                "job_id": job_id,
                "status": record.get("status"),
                "revision": record.get("revision"),
                "name": record.get("name"),
            }
        upgrade_trace["job_reads"] = reads
        assertions.add(
            "upgrade.old_tasks_readable",
            all(item.get("status") for item in reads.values()) and len(reads) == len(legacy_jobs),
            reads,
        )
        research_doc = _http_json(
            f"{candidate.base}/api/v2/research/records/{seed['research']['research_id']}"
            f"?project_id={seed['project_id']}"
        )
        upgrade_trace["research_read"] = {
            "research_id": research_doc.get("research_id"),
            "schema": research_doc.get("schema"),
            "round": research_doc.get("round"),
        }
        assertions.add(
            "upgrade.research_record_readable",
            research_doc.get("research_id") == seed["research"]["research_id"]
            and research_doc.get("schema") == "acp2.experience.research-record.v1"
            and research_doc.get("project_id") == seed["project_id"],
            upgrade_trace["research_read"],
        )

        # Deep M0 legacy semantics through the candidate's own Services code.
        code, verify, stderr = _helper(
            venv=candidate_venv,
            action="verify",
            arguments=[
                "--side",
                "candidate",
                "--run-root",
                str(run_root),
                "--jobs-db",
                str(run_root / "acp_jobs.db"),
                "--admission-db",
                str(run_root / "acp_admission.db"),
                "--seed",
                str(out / "seed.json"),
                "--out",
                str(out / "legacy-semantics.json"),
            ],
            cwd=work_dir,
            timeout=900,
            log=log_dir / "verify-candidate.log",
        )
        upgrade_trace["legacy_semantics"] = verify.get("summary")
        upgrade_trace["legacy_checks"] = verify.get("checks")
        assertions.add(
            "upgrade.legacy_semantics_all_pass",
            code == 0 and (verify.get("summary") or {}).get("status") == "PASS",
            {"exit": code, "summary": verify.get("summary"), "stderr": stderr[-400:]},
        )

        # New HTTP submit: v2 identity/digest, never the legacy key spaces.
        new_job = _http_json(
            candidate.base + "/api/v1/jobs",
            payload={
                "workflow": "singlepoint",
                "name": "candidate_http_keyed",
                "input": {"source": "S"},
                "idempotency_key": "candidate-http-key",
            },
        )
        new_job_id = str(new_job.get("job_id") or "")
        conn = sqlite3.connect(str(run_root / "acp_jobs.db"))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT identity_version, request_digest_version, scope_origin, "
                "migration_state, idempotency_key FROM idempotency_records "
                "WHERE task_id=?",
                (new_job_id,),
            ).fetchone()
        finally:
            conn.close()
        upgrade_trace["new_http_submit"] = {
            "job_id": new_job_id,
            "idempotency_key": new_job.get("idempotency_key"),
            "row": None if row is None else dict(row),
        }
        assertions.add(
            "upgrade.new_http_task_writes_v2_only",
            row is not None
            and row["identity_version"] == "2"
            and row["request_digest_version"] == "2"
            and row["migration_state"] == "native"
            and not str(row["idempotency_key"]).startswith(("op_", "auto_")),
            upgrade_trace["new_http_submit"],
        )

        # A keyless candidate MCP submit is a typed refusal with no side effect.
        before_count = _table_state(run_root / "acp_jobs.db", ("jobs",))["counts"].get("jobs", 0)
        refusal = _mcp_submit(
            venv=candidate_venv,
            base=candidate.base,
            arguments={
                "workflow": "singlepoint",
                "input": {"source": "F"},
                "project_id": "uncategorized",
            },
        )
        code_value, params = _mcp_error_code(refusal)
        after_count = _table_state(run_root / "acp_jobs.db", ("jobs",))["counts"].get("jobs", 0)
        upgrade_trace["keyless_mcp_submit"] = {
            "error_code": code_value,
            "params": params,
            "jobs_before": before_count,
            "jobs_after": after_count,
        }
        assertions.add(
            "upgrade.keyless_mcp_submit_refused_without_side_effect",
            code_value == "input_invalid"
            and params.get("rule") == "idempotency_key_required"
            and after_count == before_count,
            upgrade_trace["keyless_mcp_submit"],
        )
    finally:
        candidate.stop()
    candidate_leftover = candidate.wait_fully_stopped()
    assertions.add(
        "upgrade.controller_stopped_cleanly",
        candidate.process is None and not candidate_leftover,
        {"port": candidate.port, "leftover_pids": candidate_leftover},
    )
    upgrade_trace["state_after_stop"] = _schema_versions(run_root)
    _write_json(out / "upgrade-trace.json", upgrade_trace)

    # -- 5. rollback: restore the backup, restart the previous artifact ----
    rollback_trace: dict[str, Any] = {}
    restored_digests: dict[str, str] = {}
    _remove_tree(run_root)
    _copy_tree(backup, run_root)
    _quiesce(run_root)
    restored_digests = _tree_digests(run_root)
    restore_differing = sorted(
        set(restored_digests) ^ set(backup_digests)
        | {
            key
            for key in set(restored_digests) & set(backup_digests)
            if restored_digests[key] != backup_digests[key]
        }
    )
    assertions.add(
        "rollback.restore_is_byte_identical",
        not restore_differing,
        {"files": len(restored_digests), "differing": restore_differing[:20]},
    )
    previous_again = Controller(
        venv=previous_venv,
        run_root=run_root,
        cwd=work_dir,
        log=log_dir / "controller-rollback.log",
        side="previous-rollback",
    )
    try:
        previous_again.start()
        rollback_state = _schema_versions(run_root)
        readback = {
            label: _http_json(f"{previous_again.base}/api/v1/jobs/{item['job_id']}").get("status")
            for label, item in legacy_jobs.items()
        }
        listed = _http_json(f"{previous_again.base}/api/v1/jobs?limit=100")
        listed_ids = {
            str(item.get("id") or item.get("job_id") or "") for item in listed.get("jobs") or []
        }
        research_rollback = _http_json(
            f"{previous_again.base}/api/v2/research/records/{seed['research']['research_id']}"
            f"?project_id={seed['project_id']}"
        )
        rollback_trace.update(
            {
                "health": previous_again.health,
                "schema": rollback_state,
                "job_reads": readback,
                "listed_legacy_jobs": sorted(
                    job_id
                    for job_id in listed_ids
                    if job_id in {v["job_id"] for v in legacy_jobs.values()}
                ),
                "research_id": research_rollback.get("research_id"),
            }
        )
        assertions.add(
            "rollback.previous_schema_restored_no_half_migration",
            rollback_state["jobs"]["count"] == previous_head
            and rollback_state["prepare"] == 1
            and rollback_state["admission"] == 0
            and rollback_state["jobs"]["integrity"] == "ok",
            rollback_state,
        )
        assertions.add(
            "rollback.old_data_readable_after_rollback",
            all(bool(status) for status in readback.values())
            and len(rollback_trace["listed_legacy_jobs"]) == len(legacy_jobs)
            and research_rollback.get("research_id") == seed["research"]["research_id"],
            rollback_trace,
        )
        candidate_job_visible = _http_status(
            f"{previous_again.base}/api/v1/jobs/{upgrade_trace['new_http_submit']['job_id']}"
        )
        rollback_trace["candidate_only_job_status"] = candidate_job_visible
        assertions.add(
            "rollback.candidate_only_rows_removed_by_restore",
            candidate_job_visible == 404,
            {"status": candidate_job_visible},
        )
    finally:
        previous_again.stop()
    rollback_leftover = previous_again.wait_fully_stopped()
    rollback_trace["leftover_pids"] = rollback_leftover
    assertions.add(
        "rollback.controller_stopped_cleanly",
        previous_again.process is None and not rollback_leftover,
        {"port": previous_again.port, "leftover_pids": rollback_leftover},
    )
    _write_json(out / "rollback-trace.json", rollback_trace)

    # -- 6. interrupted migration on a copy, then a clean recovery ---------
    failure_trace: dict[str, Any] = {}
    scratch = work_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    interrupted_db = scratch / "acp_jobs.db"
    shutil.copy2(backup / "acp_jobs.db", interrupted_db)
    jobs_before = _table_state(interrupted_db, ("jobs",))["counts"].get("jobs", 0)
    code, payload, stderr = _helper(
        venv=candidate_venv,
        action="interrupt",
        arguments=[
            "--jobs-db",
            str(interrupted_db),
            "--kill-at",
            "024",
            "--out",
            str(out / "failure-injection.json"),
        ],
        cwd=work_dir,
        timeout=600,
        log=log_dir / "interrupt-migrate.log",
    )
    interrupted_state = _schema_versions(scratch)
    jobs_after = _table_state(interrupted_db, ("jobs",))["counts"].get("jobs", 0)
    failure_trace.update(
        {
            "exit": code,
            "payload": payload,
            "state_after_interrupt": interrupted_state,
            "jobs_before": jobs_before,
            "jobs_after": jobs_after,
            "stderr": stderr[-400:],
        }
    )
    assertions.add(
        "injection.migration_killed_before_024",
        code in (-9, 137, 9)
        and interrupted_state["jobs"]["count"] == previous_head
        and "024" not in interrupted_state["jobs"]["ids"],
        {"exit": code, "schema": interrupted_state["jobs"]},
    )
    assertions.add(
        "injection.jobs_never_deleted_by_interrupted_migration",
        jobs_after == jobs_before and jobs_before > 0,
        {"before": jobs_before, "after": jobs_after},
    )
    assertions.add(
        "injection.interrupted_db_is_readable_and_intact",
        interrupted_state["jobs"]["integrity"] == "ok" and interrupted_state["jobs"]["present"],
        interrupted_state["jobs"],
    )

    code, recovered, stderr = _helper(
        venv=candidate_venv,
        action="migrate",
        arguments=[
            "--jobs-db",
            str(interrupted_db),
            "--admission-db",
            str(scratch / "acp_admission.db"),
            "--out",
            str(out / "failure-recovery.json"),
        ],
        cwd=work_dir,
        timeout=600,
        log=log_dir / "recover-migrate.log",
    )
    recovered_state = _schema_versions(scratch)
    recovered_jobs = _table_state(interrupted_db, ("jobs",))["counts"].get("jobs", 0)
    failure_trace["recovery"] = {
        "exit": code,
        "state": recovered_state,
        "jobs": recovered_jobs,
        "stderr": stderr[-400:],
    }
    assertions.add(
        "injection.recovery_migrates_to_head_without_losing_jobs",
        code == 0
        and recovered_state["jobs"]["count"] == candidate_head
        and recovered_jobs == jobs_before
        and recovered_state["jobs"]["integrity"] == "ok",
        failure_trace["recovery"],
    )
    _write_json(out / "failure-injection-trace.json", failure_trace)

    return _finish(out, assertions, pair, pre_upgrade, upgrade_trace, rollback_trace, failure_trace)


def _abort(out: Path, assertions: Assertions, reason: str) -> int:
    assertions.add("harness.isolation", False, reason)
    _write_json(
        out / "summary.json",
        {
            "schema": SCHEMA,
            "status": "FAIL",
            "reason": reason,
            "evidence_kind": EVIDENCE_KIND,
            "not_production_data": True,
            "assertions": assertions.rows,
            "summary": assertions.summary(),
        },
    )
    print(json.dumps({"status": "FAIL", "reason": reason}))
    return EXIT_FAIL


def _migration_matrix(
    assertions: Assertions,
    pre_upgrade: Mapping[str, Any],
    upgrade_trace: Mapping[str, Any],
    rollback_trace: Mapping[str, Any],
    failure_trace: Mapping[str, Any],
) -> dict[str, Any]:
    """The M4-06a item matrix: old shape -> new semantics -> evidence row."""
    statuses: dict[str, str] = {row["name"]: row["status"] for row in assertions.rows}
    for check in upgrade_trace.get("legacy_checks") or []:
        statuses.setdefault(str(check.get("name")), str(check.get("status")))
    seed = pre_upgrade.get("seed") or {}
    before = pre_upgrade.get("state") or {}
    after = upgrade_trace.get("schema") or {}
    verify_state = upgrade_trace.get("legacy_semantics") or {}

    items: list[dict[str, Any]] = [
        {
            "id": "jobs.schema_head",
            "old": f"jobs-DB migrations {before.get('jobs', {}).get('count')} "
            f"({'..'.join(before.get('jobs', {}).get('ids', [])[-2:])})",
            "new": f"jobs-DB migrations {after.get('jobs', {}).get('count')} "
            f"({'..'.join(after.get('jobs', {}).get('ids', [])[-2:])})",
            "semantics": "additive migrations 024/025/027 applied; the old rows stay a prefix",
            "evidence": [
                "upgrade.schema_migrated_to_candidate_head",
                "upgrade.old_migration_rows_are_a_prefix",
            ],
        },
        {
            "id": "prepare.schema_and_keys",
            "old": f"prepare schema v{before.get('prepare')} (op_ keys, no attribution)",
            "new": f"prepare schema v{after.get('prepare')} (v1 index kept + v2 identity index)",
            "semantics": "pre-M0-05 rows keep identity_version='1' and the frozen v1 predicate; "
            "new rows are v2 (prep_ key, principal/project attribution)",
            "evidence": [
                "prepare.v1_unique_index_survives_v2_upgrade",
                "prepare.legacy_v1_row_merge_is_local_only",
                "prepare.new_row_is_v2",
            ],
        },
        {
            "id": "admission.schema_and_history",
            "old": f"admission schema {before.get('admission')} (pre-M0-07 usage rows)",
            "new": f"admission schema {after.get('admission')} (additive columns + indexes)",
            "semantics": "historical usage rows are preserved verbatim and reported unverified "
            "(binding_missing), never re-labelled trusted and never invented",
            "evidence": ["usage.historical_rows_preserved_not_fabricated"],
        },
        {
            "id": "idempotency.old_local_scope",
            "old": "identity_version 1 / legacy local scope (no attribution columns)",
            "new": "identity_version 1, scope_origin=legacy-local, migration_state=legacy-verified",
            "semantics": (
                "migration 024 classifies; a committed row with a resolvable TaskRef replays"
            ),
            "evidence": [
                "legacy.classified_committed_verified",
                "legacy.replay_verified_row_returns_original_task",
            ],
        },
        {
            "id": "idempotency.pending_and_unattributable",
            "old": "claimed rows with an expired lease / no principal-project scope",
            "new": "legacy-pending / legacy-unknown + legacy-unverified",
            "semantics": "pending is retryable, an unattributable row is a typed conflict to "
            "reconcile; neither is adopted by whoever presents the key",
            "evidence": [
                "legacy.classified_pending_not_replayable",
                "legacy.classified_unattributable_unverified",
                "legacy.unattributable_row_is_conflict_not_adopted",
            ],
        },
        {
            "id": "idempotency.cross_scope",
            "old": "one folded local scope for every HTTP caller",
            "new": "verified (principal, project) scope",
            "semantics": "the same key under another verified scope is a conflict, not a replay",
            "evidence": ["legacy.other_scope_is_conflict_not_replay"],
        },
        {
            "id": "request_digest.v1",
            "old": "request-digest version 1 (raw V1 DTO material)",
            "new": "row keeps version 1 and is verified under its own frozen field table; "
            "new rows use version 2",
            "semantics": "equal digest bytes prove the same request; a tampered payload is a "
            "typed conflict (reconcile), never a replay",
            "evidence": [
                "legacy.request_digest_v1_verifies_and_rejects_tamper",
                "new.claim_is_v2_native_and_not_legacy_namespace",
            ],
        },
        {
            "id": "mcp.auto_derived_key",
            "old": "frozen pre-M0-05 MCP derivation writes auto_ (and the old MCP transport "
            "folded it into an op_ ledger key)",
            "new": "the derivation is byte-frozen and lookup-only; keyless submit is refused",
            "semantics": "the historical row stays readable/verified; no second task is created "
            "and the namespace is never written again",
            "evidence": [
                "legacy.auto_derivation_is_byte_frozen",
                "previous.pre_m0_05_mcp_row_observed",
                "upgrade.keyless_mcp_submit_refused_without_side_effect",
                "new.no_auto_or_op_ledger_keys_written",
            ],
        },
        {
            "id": "tasks.read_only",
            "old": "pre-upgrade jobs (HTTP keyed/keyless + pre-M0-05 MCP)",
            "new": "readable through the candidate HTTP read path",
            "semantics": "old tasks stay read-only history; no rewrite, no revival",
            "evidence": ["upgrade.old_tasks_readable", "state.no_legacy_row_deleted_or_rewritten"],
        },
        {
            "id": "research.read_only",
            "old": "Proposal + ResearchRecord revisions at revision 1",
            "new": "same revision/digest through the candidate store and HTTP read",
            "semantics": "read-only read; no rewrite on upgrade",
            "evidence": [
                "research.record_and_proposal_read_only_readable",
                "upgrade.research_record_readable",
            ],
        },
        {
            "id": "snapshot.read_only",
            "old": "frozen capability/plugin descriptor snapshot (bytes)",
            "new": "same bytes served by the candidate read-only snapshot path",
            "semantics": "historical descriptor/binding/view-model bytes are never re-resolved "
            "from the active version",
            "evidence": ["snapshot.capability_freeze_read_only_readable"],
        },
        {
            "id": "backup.restore",
            "old": "pre-upgrade run root",
            "new": "real file copy, restored byte-identically on rollback",
            "semantics": "backup before migration, restore before rollback",
            "evidence": ["backup.file_copy_matches_source", "rollback.restore_is_byte_identical"],
        },
        {
            "id": "rollback.previous_head",
            "old": f"previous head {before.get('jobs', {}).get('count')}",
            "new": (
                f"rolled back to {rollback_trace.get('schema', {}).get('jobs', {}).get('count')}"
            ),
            "semantics": (
                "previous Controller restarts on the restored data; no half-migrated state"
            ),
            "evidence": [
                "rollback.previous_schema_restored_no_half_migration",
                "rollback.old_data_readable_after_rollback",
                "rollback.candidate_only_rows_removed_by_restore",
            ],
        },
        {
            "id": "failure.interrupted_migration",
            "old": f"pre-upgrade jobs DB ({failure_trace.get('jobs_before')} jobs)",
            "new": (
                "SIGKILL before 024: schema "
                f"{failure_trace.get('state_after_interrupt', {}).get('jobs', {}).get('count')}, "
                f"{failure_trace.get('jobs_after')} jobs intact; recovery migrates to head"
            ),
            "semantics": "interrupted transaction leaves a readable DB, no bare job delete and "
            "no partially recorded migration; a later migrate completes",
            "evidence": [
                "injection.migration_killed_before_024",
                "injection.jobs_never_deleted_by_interrupted_migration",
                "injection.interrupted_db_is_readable_and_intact",
                "injection.recovery_migrates_to_head_without_losing_jobs",
            ],
        },
    ]
    for item in items:
        item_statuses = [statuses.get(name, "MISSING") for name in item["evidence"]]
        item["status"] = "PASS" if all(value == "PASS" for value in item_statuses) else "FAIL"
        item["evidence_status"] = dict(zip(item["evidence"], item_statuses, strict=True))
    return {
        "schema": "acp2.upgrade-pair.migration-matrix.v1",
        "task": "M4-06a",
        "previous_ref": PREVIOUS_REF,
        "items": items,
        "status": "PASS" if all(item["status"] == "PASS" for item in items) else "FAIL",
        "verify_checks": verify_state,
        "seed_identifiers": {
            "claims": seed.get("claims"),
            "prepare": seed.get("prepare", {}).get("operation_id"),
            "research": seed.get("research", {}).get("research_id"),
            "usage": seed.get("usage", {}).get("usage_id"),
            "capability": seed.get("capability", {}).get("capability_id"),
        },
    }


def _write_readme(out: Path, document: Mapping[str, Any], matrix: Mapping[str, Any]) -> None:
    """Generate the self-describing evidence README from the drilled facts."""
    pair = document["upgrade_pair"]
    previous = pair["previous"]["source"] or {}
    candidate = pair["candidate"]["source"] or {}
    before = document["before"]["schema"]
    after = document["upgrade"]["schema"] or {}
    rollback = document["rollback"]["schema"] or {}
    failure = document["failure_injection"]
    rows = [
        "| # | Item | Status |",
        "|---|---|---|",
    ]
    for index, item in enumerate(matrix["items"], start=1):
        rows.append(f"| {index} | `{item['id']}` | {item['status']} |")
    text = "\n".join(
        [
            "# M4-06a — migration / upgrade / rollback drill",
            "",
            f"**Status: {document['status']}** · evidence_kind=`{document['evidence_kind']}` · "
            f"`not_production_data={str(document['not_production_data']).lower()}`",
            "",
            "Generated by `scripts/acceptance/acp2_upgrade_pair.py`. Isolated run root under the",
            "harness work dir; the production run root is never used.",
            "",
            "## Upgrade pair",
            "",
            "| side | revision | tree | clean | dirty paths |",
            "|---|---|---|---|---|",
            f"| previous (`{PREVIOUS_REF}`) | `{str(previous.get('revision'))[:12]}` | "
            f"`{str(previous.get('tree'))[:12]}` | {previous.get('clean')} | "
            f"`{previous.get('dirty_paths')}` |",
            f"| candidate | `{str(candidate.get('revision'))[:12]}` | "
            f"`{str(candidate.get('tree'))[:12]}` | {candidate.get('clean')} | "
            f"`{candidate.get('dirty_paths')}` |",
            "",
            "Both release manifests are archived next to this file and every artifact digest is",
            "re-verified against the bytes on disk (`pair.*.manifest_digests_match`).",
            "",
            "## Schema heads",
            "",
            f"- jobs DB: `{before.get('jobs', {}).get('count')}` "
            f"→ `{after.get('jobs', {}).get('count')}`",
            f"- prepare: `{before.get('prepare')}` → `{after.get('prepare')}`",
            f"- admission: `{before.get('admission')}` → `{after.get('admission')}`",
            f"- rollback restores: jobs `{rollback.get('jobs', {}).get('count')}`, "
            f"prepare `{rollback.get('prepare')}`, admission `{rollback.get('admission')}`",
            "",
            "## Migration item matrix",
            "",
            *rows,
            "",
            "Per-item old→new semantics and the exact assertion names are in",
            "`migration-matrix.json`; raw traces are `upgrade-trace.json`,",
            "`legacy-semantics.json`, `rollback-trace.json`,",
            "`failure-injection-trace.json`, `previous-state.json`, `seed.json`.",
            "",
            "## Failure injection",
            "",
            f"- SIGKILL before migration 024: exit `{failure.get('exit')}`, schema stays",
            f"  `{failure.get('state_after_interrupt', {}).get('jobs', {}).get('count')}`, "
            f"{failure.get('jobs_after')} jobs intact, `integrity_check` ok",
            "- clean recovery migrates the same file to the candidate head without losing jobs",
            "",
            "## Reproduce",
            "",
            "```bash",
            "PYTHONPATH=src python3.11 scripts/acceptance/acp2_upgrade_pair.py \\",
            f"    --out {out}",
            "PYTHONPATH=src python3.11 -m pytest tests/test_acp2_upgrade_pair.py \\",
            "    tests/test_acp2_runtime_migration.py -q --run-slow",
            "```",
            "",
        ]
    )
    (out / "README.md").write_text(text, encoding="utf-8")


def _finish(
    out: Path,
    assertions: Assertions,
    pair: Mapping[str, Any],
    pre_upgrade: Mapping[str, Any],
    upgrade_trace: Mapping[str, Any],
    rollback_trace: Mapping[str, Any],
    failure_trace: Mapping[str, Any],
) -> int:
    summary = assertions.summary()
    document = {
        "schema": SCHEMA,
        "task": "M4-06a",
        "status": summary["status"],
        "evidence_kind": EVIDENCE_KIND,
        "not_production_data": True,
        "isolation": {
            "production_run_root_untouched": True,
            "work_dir": str(pre_upgrade.get("run_root", "")),
            "note": (
                "isolated run root under the harness work dir; no production service or data used"
            ),
        },
        "upgrade_pair": {
            "previous": pair["artifacts"]["previous"],
            "candidate": pair["artifacts"]["candidate"],
            "installs": pair.get("installs"),
            "probes": pair.get("probes"),
            "candidate_dirty_paths": pair.get("candidate_dirty_paths"),
        },
        "before": {
            "schema": pre_upgrade["state"],
            "jobs_db": pre_upgrade["jobs_db"]["counts"],
            "admission_db": pre_upgrade["admission_db"]["counts"],
            "research_db": pre_upgrade["research_db"]["counts"],
        },
        "upgrade": {
            "schema": upgrade_trace.get("schema"),
            "legacy_semantics": upgrade_trace.get("legacy_semantics"),
            "new_task": upgrade_trace.get("new_http_submit"),
            "keyless_mcp": upgrade_trace.get("keyless_mcp_submit"),
        },
        "rollback": {
            "schema": rollback_trace.get("schema"),
            "job_reads": rollback_trace.get("job_reads"),
        },
        "failure_injection": {
            "exit": failure_trace.get("exit"),
            "state_after_interrupt": failure_trace.get("state_after_interrupt"),
            "recovery": failure_trace.get("recovery"),
        },
        "assertions": assertions.rows,
        "summary": summary,
    }
    matrix = _migration_matrix(
        assertions, pre_upgrade, upgrade_trace, rollback_trace, failure_trace
    )
    _write_json(out / "migration-matrix.json", matrix)
    _write_readme(out, document, matrix)
    document["migration_matrix"] = {
        "status": matrix["status"],
        "items": len(matrix["items"]),
        "path": "migration-matrix.json",
    }
    _write_json(out / "summary.json", document)
    print(json.dumps({"status": summary["status"], "summary": summary}, indent=2))
    return EXIT_PASS if summary["status"] == "PASS" else EXIT_FAIL


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="evidence output directory")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
        help="isolated artifact store holding the two release sets and their venvs",
    )
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--previous-ref", default=PREVIOUS_REF)
    parser.add_argument(
        "--candidate-source",
        default="",
        help="candidate source tree (default: temporary worktree at HEAD)",
    )
    parser.add_argument("--reuse-artifacts", action="store_true")
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument("--timeout", type=float, default=1800.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run_drill(args)
    except Exception as exc:  # pragma: no cover - harness failure path
        import traceback

        out = Path(args.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        _write_json(
            out / "summary.json",
            {
                "schema": SCHEMA,
                "status": "FAIL",
                "reason": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "evidence_kind": EVIDENCE_KIND,
                "not_production_data": True,
            },
        )
        print(json.dumps({"status": "FAIL", "reason": f"{type(exc).__name__}: {exc}"}, indent=2))
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
