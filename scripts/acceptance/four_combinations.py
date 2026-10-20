#!/usr/bin/env python3
"""Four-combination acceptance harness (F0/S0, F1/S0, F0/S1, F1/S1).

F = the frontend/static bundle from a git revision.
S = the service implementation from a git revision.

The harness prepares detached worktrees for F0 (the frozen baseline) and F1
(the code candidate), starts S0/S1 on different ports with temporary
``ACP_RUN_ROOT`` values, probes the retained old API surface, runs the seven
legacy workflows through their ordinary CLI entry (missing-argument form, so
no QC is required), and writes one JSON result per cell plus ``summary.json``.

Contracted by ``reports/architecture_remediation_20261020/harness-contract.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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
    workflow_ids = [
        str(item.get("id"))
        for item in (workflows if isinstance(workflows, list) else [])
        if isinstance(item, dict) and item.get("id")
    ]
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
        "nmr": ["run", "nmr", "--input", "CCO"],
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
) -> dict[str, Any]:
    selected = list(cells or CELL_NAMES)
    for name in selected:
        if name not in CELL_NAMES:
            raise HarnessError(f"unknown cell {name!r}")
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
        ports = {"S0": _free_port(), "S1": _free_port()}
        service_results: dict[str, dict[str, Any]] = {}
        legacy_results: dict[str, list[dict[str, Any]]] = {}
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
        summary: dict[str, Any] = {
            "schema": SUMMARY_SCHEMA,
            "scenarios": [],
            "all_pass": True,
        }
        for name in selected:
            front, service = name.split("/")
            tree = worktrees[front]
            document = {
                "schema": SCHEMA,
                "harness": "four_combinations.py",
                "harness_version": "1",
                "scenario": name,
                "candidate_revision": f1,
                "f0_revision": f0,
                "f1_revision": f1,
                "environment": {
                    "python": sys.version.split()[0],
                    "repo": str(REPO_ROOT),
                    "service_port": ports[service],
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
                        "id": "legacy_workflows",
                        "command": "python -m acp.cli run <legacy> (missing-argument form)",
                        "exit_code": 0 if all(row["ok"] for row in legacy_results[service]) else 1,
                        "assertions": legacy_results[service],
                        "ok": all(row["ok"] for row in legacy_results[service]),
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
                    "service": service_results[service],
                    "legacy": legacy_results[service],
                },
            }
            ok = service_results[service]["ok"] and all(
                row["ok"] for row in legacy_results[service]
            )
            if not ok:
                document["result"] = "FAIL"
            summary["all_pass"] = summary["all_pass"] and ok
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
        summary = execute(f0=args.f0_revision, f1=args.f1_revision, out=Path(args.out), cells=cells)
    except (HarnessError, OSError, subprocess.SubprocessError) as exc:
        print(f"HARNESS ERROR: {exc}", file=sys.stderr)
        return 1
    return 0 if summary["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
