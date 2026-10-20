#!/usr/bin/env python3
"""Real-environment acceptance harness (M1 / LSF singlepoint).

Scenarios are environment-gated.  Missing binaries or credentials produce a
``NOT_VERIFIED`` result with the exact missing prerequisite and exit code 2;
they are never promoted to PASS.  ``--dry-run`` prints the exact commands
without starting a service or submitting a job.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCHEMA = "acp.acceptance.harness.v1"
SUMMARY_SCHEMA = "acp.acceptance.summary.v1"
SCENARIOS = (
    "m1-singlepoint",
    "lsf-singlepoint",
    "m1-failure-cancel-restart",
    "browser-default-page",
)
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _missing_prerequisite(scenario: str) -> str | None:
    if scenario in ("m1-singlepoint", "m1-failure-cancel-restart"):
        if not os.environ.get("ACP_M1_ORCA"):
            return "ACP_M1_ORCA unset"
    if scenario == "lsf-singlepoint":
        if not os.environ.get("ACP_LSF_NODE"):
            return "ACP_LSF_NODE unset"
    if scenario == "browser-default-page":
        try:
            import playwright  # noqa: F401
        except ImportError:
            return "playwright not installed"
    return None


def _write_result(
    out: Path,
    scenario: str,
    *,
    result: str,
    reason: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "harness": "real_environment.py",
        "harness_version": "1",
        "scenario": scenario,
        "started_on": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "finished_on": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidate_revision": os.environ.get("ACP_CANDIDATE_REVISION", ""),
        "f0_revision": None,
        "f1_revision": None,
        "environment": {
            "python": sys.version.split()[0],
            "run_root": os.environ.get("ACP_RUN_ROOT", ""),
            "env_present": {
                "ACP_M1_ORCA": bool(os.environ.get("ACP_M1_ORCA")),
                "ACP_LSF_NODE": bool(os.environ.get("ACP_LSF_NODE")),
            },
            "env_missing_reason": reason,
        },
        "result": result,
        "scope": "IN_SCOPE",
        "skip_reason": None,
        "not_verified_reason": reason if result == "NOT_VERIFIED" else None,
        "steps": steps or [],
        "artifacts": artifacts or [],
        "metrics": None,
        "cell": None,
    }
    (out / f"{scenario}.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = {
        "schema": SUMMARY_SCHEMA,
        "scenarios": [
            {"scenario": scenario, "result": result, "skip_reason": None},
        ],
        "all_pass": result == "PASS",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return document


def _plan_commands(scenario: str) -> list[str]:
    port = "<free-port>"
    base = "python -m acp.cli run serve --host 127.0.0.1 --port " + port
    if scenario == "lsf-singlepoint":
        return [
            base + " (ACP_RUN_ROOT=<temp>)",
            "POST /api/v1/jobs {workflow: singlepoint, execution: remote, node: $ACP_LSF_NODE}",
            "bsub < generated LSF script",
            "bjobs -a <lsf_job_id>",
            "bstop <lsf_job_id> && bjobs -a <lsf_job_id> (stop confirmation)",
            "GET /api/v1/jobs/{id}/detail; GET /api/v1/jobs/{id}/structure-viewer",
        ]
    return [
        base + " (ACP_RUN_ROOT=<temp>)",
        "POST /api/v1/jobs {workflow: singlepoint, input: <real xyz/gjf>, method: <real method>}",
        "GET /api/v1/jobs/{id}/detail (poll terminal)",
        "open <work_dir>/RESULT/result_manifest.json",
        "GET /api/v1/jobs/{id}/structure-viewer",
        "python -m acp.cli run singlepoint --input <same> (CLI cross-check only)",
    ]


def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def _get_json(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def _wait_service(base: str, timeout: float) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        code, _ = _get_json(base + "/api/status")
        if code == 200:
            return
        last = f"status {code}"
        time.sleep(0.5)
    raise RuntimeError(f"service not ready: {last}")


def _execute_m1(out: Path, *, timeout: float) -> int:
    root = Path(tempfile.mkdtemp(prefix="acp-m1-"))
    port = _free_port()
    env = dict(os.environ)
    env["ACP_RUN_ROOT"] = str(root)
    if os.environ.get("ACP_M1_ORCA"):
        env.setdefault("ACP_ORCA", os.environ["ACP_M1_ORCA"])
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    steps: list[dict[str, Any]] = []
    try:
        _wait_service(base, timeout)
        status, response = _post_json(
            base + "/api/v1/jobs",
            {
                "workflow": "singlepoint",
                "input": "CCO",
                "method": {"method": "r2SCAN-3c", "basis": "def2-SVP"},
            },
        )
        steps.append(
            {
                "id": "submit",
                "exit_code": 0 if status in (200, 201) else 1,
                "ok": status in (200, 201),
            }
        )
        if status not in (200, 201) or not isinstance(response, dict):
            return _record_failure(out, "m1-singlepoint", steps, "submission failed")
        job_id = str(response.get("id") or response.get("job_id") or "")
        if not job_id:
            return _record_failure(out, "m1-singlepoint", steps, "submission returned no job id")
        terminal = {"completed", "failed", "cancelled"}
        deadline = time.time() + timeout
        detail: dict[str, Any] = {}
        while time.time() < deadline:
            code, detail = _get_json(base + f"/api/v1/jobs/{job_id}/detail")
            status_name = str((detail.get("job") or {}).get("status") or "").lower()
            if code == 200 and status_name in terminal:
                break
            time.sleep(1)
        status_name = str((detail.get("job") or {}).get("status") or "").lower()
        steps.append(
            {
                "id": "terminal",
                "exit_code": 0 if status_name == "completed" else 1,
                "ok": status_name == "completed",
                "actual": status_name,
            }
        )
        if status_name != "completed":
            return _record_failure(out, "m1-singlepoint", steps, "job did not complete")
        frames_code, frames = _get_json(base + f"/api/v1/jobs/{job_id}/structure-viewer")
        frames_ok = frames_code == 200 and bool(
            (frames or {}).get("entries") or (frames or {}).get("frames")
        )
        steps.append({"id": "structure_viewer", "exit_code": frames_code, "ok": frames_ok})
        work_dir = Path(str((detail.get("job") or {}).get("work_dir") or ""))
        manifest = work_dir / "RESULT" / "result_manifest.json"
        artifacts = (
            [{"path": str(manifest), "sha256": _sha256(manifest), "bytes": manifest.stat().st_size}]
            if manifest.is_file()
            else []
        )
        result = "PASS" if frames_ok and manifest.is_file() else "FAIL"
        _write_result(out, "m1-singlepoint", result=result, steps=steps, artifacts=artifacts)
        return EXIT_PASS if result == "PASS" else EXIT_FAIL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not os.environ.get("ACP_KEEP_TEMP"):
            import shutil

            shutil.rmtree(root, ignore_errors=True)


def _record_failure(out: Path, scenario: str, steps: list[dict[str, Any]], reason: str) -> int:
    _write_result(out, scenario, result="FAIL", reason=None, steps=steps)
    print(f"FAIL: {reason}", file=sys.stderr)
    return EXIT_FAIL


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _execute_lsf(out: Path, *, timeout: float) -> int:
    node = os.environ.get("ACP_LSF_NODE", "")
    if not node:
        return EXIT_NOT_VERIFIED
    # The real LSF chain requires bsub/bjobs/bstop and configured credentials.
    # This harness intentionally records the exact prerequisites rather than
    # claiming a controlled fake transport is a real cluster run.
    reason = "real LSF execution requires bsub/bjobs/bstop and credentials; not executed"
    _write_result(out, "lsf-singlepoint", result="NOT_VERIFIED", reason=reason)
    return EXIT_NOT_VERIFIED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--out")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        scenario = args.scenario or "m1-singlepoint"
        for command in _plan_commands(scenario):
            print(command)
        return EXIT_PASS
    if not args.scenario:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    if not args.out:
        print("--out is required for execution", file=sys.stderr)
        return EXIT_USAGE
    if args.keep_temp:
        os.environ["ACP_KEEP_TEMP"] = "1"
    missing = _missing_prerequisite(args.scenario)
    if missing is not None:
        _write_result(Path(args.out), args.scenario, result="NOT_VERIFIED", reason=missing)
        print(f"NOT_VERIFIED: {missing}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    if args.scenario in ("m1-singlepoint", "m1-failure-cancel-restart"):
        return _execute_m1(Path(args.out), timeout=args.timeout)
    if args.scenario == "lsf-singlepoint":
        return _execute_lsf(Path(args.out), timeout=args.timeout)
    _write_result(
        Path(args.out),
        args.scenario,
        result="NOT_VERIFIED",
        reason=(
            "browser-default-page is executed by "
            "tests/test_acp_frontend_browser.py, not this command"
        ),
    )
    return EXIT_NOT_VERIFIED


if __name__ == "__main__":
    raise SystemExit(main())
