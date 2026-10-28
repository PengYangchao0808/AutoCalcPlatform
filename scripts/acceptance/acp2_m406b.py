#!/usr/bin/env python3.11
"""M4-06b acceptance — cross-domain reuse and multi-tenant/multi-hypothesis isolation.

The task closes the second half of H6 (``M4-06a`` is the first half):

======================  =========================================================
row                     what must be true (and what evidence closes it)
======================  =========================================================
``cross_domain_nmr``    NMR reuses the *same* platform capability catalog, the
                        *same* research-budget ledger identity and the *same*
                        M1-03 evidence projection and frame/ViewModel contract as
                        thermochemistry.  A real xTB optimisation provides the
                        structure; the GIAO shielding table is the declared
                        fixture stub and the experimental peak list is the
                        declared fixture spectrum.  The comparison itself is the
                        real ``cccp.analysis.nmr.comparison`` implementation.
``cross_domain_thermochemistry``
                        Thermochemistry recomputes the Shermo product from the
                        **real M4-04 route-a** endpoint frequency log and
                        single-point energy (native inputs, digest-verified),
                        then feeds a real one-component ensemble analysis.  Same
                        capability catalog, same budget ledger, same evidence
                        projection and same frame contract.
``multitenant_matrix``  3 projects x 3 users x 2 hypotheses concurrently:
                        research memory (Proposal CAS), cache/capability scope,
                        approvals, budgets, TaskRef and artifacts assert item by
                        item that nothing crosses a project.
``revocation_three_face``
                        one key is revoked: HTTP, MCP stdio and the remote CLI
                        task surface all stop answering for it at the same time.
``consistency``         cross-project read denial, budget non-transfer, event
                        stream isolation, plugin snapshot per project and
                        capability-forms per project scope.
``h6_input``            the M4-06a + M4-06b verdict, with the coverage change the
                        orchestrator may apply (this run never edits coverage).
======================  =========================================================

Hard rules obeyed by the run:

* the run root, identity DB, jobs DB, admission DB and research-resource DB are
  isolated under ``/ACPServer/acp2-runs/remediation/m4-06b``; the production
  run root is never used;
* the real Controller is a uvicorn child; MCP is a real ``acp mcp serve`` stdio
  child and the CLI is a real ``acp task query --controller`` child;
* real QC is limited to short xTB and Shermo runs; the expensive NMR GIAO chain
  is a **declared stub boundary** recorded as such, never presented as a live
  shielding calculation;
* the M4-04 route-a inputs are re-used read-only and digest-verified;
* no browser, no rph, no execution-limit flip, no manifest/AGENTS change.

Usage::

    set -a; . /ACPServer/acp2-runs/remediation/env.sh; set +a
    PYTHONPATH=src python3.11 scripts/acceptance/acp2_m406b.py \
        --out reports/acp2_remediation_20261026/m4-06b/
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import queue
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

RUN_ROOT = Path("/ACPServer/acp2-runs/remediation/m4-06b")
SCHEMA = "acp2.m406b.acceptance.v1"
PYTHON = sys.executable
PROJECTS = ("project_m406b_alpha", "project_m406b_beta", "project_m406b_gamma")
USERS = {
    PROJECTS[0]: ("principal_m406b_alice", "m406b-alice"),
    PROJECTS[1]: ("principal_m406b_bob", "m406b-bob"),
    PROJECTS[2]: ("principal_m406b_carol", "m406b-carol"),
}
HYPOTHESES = ("hypothesis.h1", "hypothesis.h2")
TENANT = "acp-m406b"
ROUTE_A_CHAIN = REPO / "reports" / "acp2_remediation_20261026" / "m4-04-route-a" / "chain"
ROUTE_A_FREQ_LOG = Path(
    "/ACPServer/acp2-runs/remediation/m4-04-route-a-reflevel/da/ts_frequency/ts_frequency/freq.out"
)
NMR_FIXTURE = (
    REPO
    / "tests"
    / "fixtures"
    / "acp2"
    / "science"
    / "cross_domain"
    / ("nmr_shielding_constructed.json")
)
PLUGIN_SNAPSHOT_FIXTURE = (
    REPO
    / "tests"
    / "fixtures"
    / "acp2"
    / "science"
    / "external_plugin"
    / ("cccp_science_plugin.json")
)

#: Ethanol (CCO) in a real, non-degenerate geometry; the xTB optimisation below
#: is a real subprocess, this is only its starting structure.
ETHANOL_XYZ = """9
ethanol start geometry
C -1.20000 0.20000 0.00000
C 0.00000 -0.60000 0.00000
O 1.10000 0.20000 0.00000
H -1.30000 0.90000 0.80000
H -1.30000 0.90000 -0.80000
H -2.00000 -0.40000 0.00000
H 0.00000 -1.30000 0.90000
H 0.00000 -1.30000 -0.90000
H 1.90000 -0.20000 0.00000
"""


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class StepError(RuntimeError):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "data": self.data,
        }


def check(name: str, ok: bool, detail: str = "", **data: Any) -> Check:
    return Check(name, "PASS" if ok else "FAIL", detail, dict(data))


def free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Controller child
# ---------------------------------------------------------------------------


class ControllerProcess:
    """A real Controller (uvicorn) bound to an isolated run root."""

    def __init__(self, *, workdir: Path, run_root: Path, log_path: Path, env: Mapping[str, str]):
        self.workdir = workdir
        self.run_root = run_root
        self.log_path = log_path
        self.env = dict(env)
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.calls: list[dict[str, Any]] = []

    def start(self, timeout_s: float = 90.0) -> None:
        child_env = dict(os.environ)
        child_env["PYTHONPATH"] = str(SRC)
        child_env["ACP_RUN_ROOT"] = str(self.run_root)
        child_env.update(self.env)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                PYTHON,
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
            cwd=str(self.workdir),
            env=child_env,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise StepError("controller.start", "controller exited during startup")
            try:
                status, _ = self.request("GET", "/api/status", timeout=2)
                if status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.2)
        raise StepError("controller.start", "controller did not answer /api/status")

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timeout: float = 120.0,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **dict(headers or {})},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw[:400]
        self.calls.append({"method": method, "path": path, "status": status, "ms": elapsed_ms})
        return status, parsed

    def download(
        self, path: str, *, headers: Mapping[str, str], timeout: float = 60.0
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(self.base_url + path, method="GET", headers=dict(headers))
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
        self.calls.append(
            {
                "method": "GET",
                "path": path,
                "status": status,
                "ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
        return status, raw

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
            self.process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def log_tail(self, limit: int = 6000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8")[-limit:]
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# MCP stdio child
# ---------------------------------------------------------------------------


class McpProcess:
    """One real ``acp mcp serve`` stdio process (JSON-RPC newline frames)."""

    def __init__(self, run_root: Path, base: str, *, api_key: str | None) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        env["ACP_RUN_ROOT"] = str(run_root)
        env.pop("ACP_API_KEY", None)
        if api_key is not None:
            env["ACP_API_KEY"] = api_key
        self.process = subprocess.Popen(
            [
                PYTHON,
                "-m",
                "acp",
                "mcp",
                "serve",
                "--transport",
                "stdio",
                "--controller",
                base,
                "--log-level",
                "ERROR",
            ],
            cwd=str(REPO),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._next_id = 1
        self.frames: queue.Queue[bytes | None] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.frames.put(line)
        self.frames.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr.append(line.decode("utf-8", errors="replace"))

    def request(self, method: str, params: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        assert self.process.stdin is not None
        self.process.stdin.write(
            (
                json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                + "\n"
            ).encode()
        )
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(method)
            try:
                line = self.frames.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(method) from None
            if line is None:
                raise RuntimeError("mcp stdout closed: " + "".join(self.stderr)[-1200:])
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "m406b-acceptance", "version": "1"},
            },
        )
        assert self.process.stdin is not None
        self.process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.process.stdin.flush()

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def mcp_data(result: Mapping[str, Any]) -> dict[str, Any]:
    return dict((result.get("structuredContent") or {}).get("data") or {})


def mcp_error_code(result: Mapping[str, Any]) -> str:
    return str(((result.get("structuredContent") or {}).get("error") or {}).get("code") or "")


def run_cli(
    args: Sequence[str], *, env_extra: Mapping[str, str], timeout: float = 60.0
) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env.update(env_extra)
    completed = subprocess.run(
        [PYTHON, "-m", "acp", *args],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    payload: Any = None
    for line in reversed(completed.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    return {
        "argv": [PYTHON, "-m", "acp", *args],
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-1200:],
        "stderr_tail": completed.stderr[-1200:],
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Environment / identity provisioning
# ---------------------------------------------------------------------------


@dataclass
class Context:
    out: Path
    work: Path
    run_root: Path
    identity_db: Path
    jobs_db: Path
    admission_db: Path
    resources_db: Path
    controller: ControllerProcess | None = None
    accounts: dict[str, Any] = field(default_factory=dict)
    keys: dict[str, Any] = field(default_factory=dict)
    lifecycle: Any = None
    key_manager: Any = None
    release: dict[str, str] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    records: dict[str, Any] = field(default_factory=dict)

    def record(self, name: str, payload: Any, *, digest: bool = True) -> dict[str, Any]:
        path = self.out / name
        write_json(path, payload)
        entry = {"path": name, "sha256": file_digest(path) if digest else None}
        self.records[name] = entry
        return entry

    def add(self, item: Check) -> Check:
        self.checks.append(item)
        return item


def git_facts() -> dict[str, Any]:
    def run(*argv: str) -> str:
        completed = subprocess.run(
            ["git", *argv], cwd=str(REPO), capture_output=True, text=True, timeout=20
        )
        return completed.stdout.strip()

    return {
        "head": run("rev-parse", "HEAD"),
        "tree": run("rev-parse", "HEAD^{tree}"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_paths": [line for line in run("status", "--short").splitlines() if line],
    }


def find_shermo() -> str:
    """Resolve Shermo from the env override, PATH or the host's conventional path.

    The remediation profile does not export ``CONFSEARCH_SHERMO_PATH`` on this
    host, so the acceptance harness resolves the same binary a user would point
    the config at and records the resolved path in the receipt.
    """
    candidates = [
        os.environ.get("CONFSEARCH_SHERMO_PATH") or "",
        shutil.which("Shermo") or "",
        shutil.which("shermo") or "",
        "/home/xieningke/bin/Shermo",
        "/home/xieningke/Shermo_2.6.1/Shermo",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return ""


def local_release() -> dict[str, str]:
    pyproject = REPO / "pyproject.toml"
    revision = git_facts()["head"]
    digest = digest_bytes(pyproject.read_bytes() + revision.encode())
    return {"release_id": digest.split(":")[1][:16], "release_digest": digest}


def provision_identity(ctx: Context) -> None:
    from acp_services.workspace.identity_lifecycle import (
        IdentityLifecycle,
        IdentityLifecyclePolicy,
    )
    from acp_services.workspace.keys import ApiKeyManager

    db_path = ctx.identity_db
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
    ctx.key_manager = key_manager
    ctx.lifecycle = lifecycle
    for project in PROJECTS:
        principal_id, login_id = USERS[project]
        invitation = lifecycle.invite_account(
            principal_id=principal_id, login_id=login_id, roles=["member"]
        )
        account = lifecycle.accept_invitation(
            invitation.token, password=f"M406b-Strong-Passw0rd-{login_id}!"
        )
        key = lifecycle.issue_key(
            account.account_id,
            scope="workspace",
            actions=["read", "write", "control"],
            project=project,
        )
        ctx.accounts[project] = account
        ctx.keys[project] = key


def seed_projects_and_tasks(ctx: Context) -> None:
    from acp.scheduler.events import JobEventLog
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp_services.store.layout import runtime_file
    from acp_services.store.migrations import migrate
    from acp_services.store.store import JobStore

    migrate(ctx.jobs_db)
    conn = sqlite3.connect(str(ctx.jobs_db))
    try:
        now = utc_now()
        for project in PROJECTS:
            conn.execute(
                """INSERT OR IGNORE INTO projects
                       (project_id, name, description, tags, run_root, settings,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (project, project, "M4-06b isolated project", "[]", "", "{}", now, now),
            )
        conn.commit()
    finally:
        conn.close()

    store = JobStore(ctx.jobs_db)
    from acp_services.runtime.capability_registry import write_read_only_snapshot

    plugin_descriptor = (
        read_json(PLUGIN_SNAPSHOT_FIXTURE) if PLUGIN_SNAPSHOT_FIXTURE.is_file() else {}
    )
    for index, project in enumerate(PROJECTS):
        task_id = f"m406b-{project.split('_')[-1]}-task"
        work_dir = ctx.run_root / task_id
        work_dir.mkdir(parents=True, exist_ok=True)
        # Same relative artifact path in every project, different bytes: a
        # cross-project reader would be caught by the digest, not only the status.
        artifact = work_dir / "RESULT" / "native_artifact.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            f"project={project}\nindex={index}\nnonce={uuid.uuid4().hex}\n", encoding="utf-8"
        )
        plugin_dir = work_dir / "WORK" / "00_RUNTIME" / "frozen" / "plugin_snapshot"
        write_read_only_snapshot(
            plugin_dir,
            {
                "descriptor": plugin_descriptor,
                "freeze": {
                    "schema": "acp2.capability.freeze.v1",
                    "project_id": project,
                    "capability_id": "acp2_external_native_singlepoint",
                    "descriptor_digest": digest_bytes(
                        json.dumps(plugin_descriptor, sort_keys=True).encode()
                    ),
                    "frozen_by": "m4-06b-acceptance",
                },
            },
        )
        JobEventLog(runtime_file(work_dir, "events.jsonl")).append(
            "job.paused",
            idempotency_key=f"seed:{task_id}",
            stage="paused",
            job_id=task_id,
            project_id=project,
        )
        manager = store
        manager.create(
            JobRecord(
                id=task_id,
                spec=JobSpec(workflow="fake", name=task_id, project_id=project),
                status=JobStatus.PAUSED,
                work_dir=str(work_dir),
                project_id=project,
                group_id=task_id,
            )
        )
        ctx.records.setdefault("seeded_tasks", {})[project] = {
            "task_id": task_id,
            "work_dir": str(work_dir),
            "artifact_sha256": file_digest(artifact),
            "artifact_bytes": artifact.stat().st_size,
            "plugin_snapshot_sha256": file_digest(plugin_dir / "descriptor.json"),
            "plugin_freeze_sha256": file_digest(plugin_dir / "freeze.json"),
        }


def define_budgets(ctx: Context) -> dict[str, Any]:
    from acp_services.runtime.admission import ReservationLedger
    from acp_services.runtime.research_budget import budget_id_for, parse_budget_policy

    ledger = ReservationLedger(str(ctx.admission_db))
    policies: dict[str, Any] = {}
    for project in PROJECTS:
        for hypothesis in HYPOTHESES:
            budget_id = budget_id_for(project, hypothesis)
            policy = parse_budget_policy(
                {
                    "units": {"task_ref": 4, "qc_invocation": 8, "attempt": 4},
                    "timeouts": {"science_runtime_s": 900, "wall_seconds": 900},
                },
                budget_id=budget_id,
                approval_ref=f"ap_m406b_{project}_{hypothesis.replace('.', '_')}",
                project_id=project,
            )
            ledger.define_research_budget(policy)
            policies[budget_id] = policy.to_dict()
    return {"ledger": ledger, "policies": policies, "db": str(ctx.admission_db)}


# ---------------------------------------------------------------------------
# Cross-domain rows
# ---------------------------------------------------------------------------


def _worker_attempt(
    *,
    work_dir: Path,
    task_id: str,
    task: str,
    result: Mapping[str, Any],
    report_bytes: bytes,
    release: Mapping[str, str],
) -> dict[str, Any]:
    """Write the worker-contract artifacts around a real task result."""
    from acp_services.contracts.envelope import ExecutionReceipt
    from acp_services.runtime import worker

    body = {
        "schema": worker.TASK_RESULT_SCHEMA,
        "task": task,
        "status": "completed",
        "complete": True,
        "truth_table_row": "a",
        "qualification": worker.QUALIFICATION_COMPLETE,
        "partial_result": False,
        "normal_reuse": False,
        "publish": True,
        "publish_policy": "normal",
        "receipt_state": "completed",
        "job_status": "completed",
        "exit_code": 0,
        "required_facts": sorted(result.keys()),
        "missing_required_facts": [],
        "authority": "acp2-m406b.acceptance",
        "result_digest": digest_bytes(
            json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        ),
        "written_at": utc_now(),
        "result": dict(result),
    }
    document = {**body, "content_digest": worker.task_result_content_digest(body)}
    worker.write_task_result_document(work_dir, document)
    receipt = ExecutionReceipt(
        receipt_version=1,
        job_id=task_id,
        attempt=1,
        submission_id=f"m406b-{task_id}",
        state="completed",
        result_digest=str(body["result_digest"]),
        written_at=utc_now(),
        release_id=release["release_id"],
        release_digest=release["release_digest"],
        owner="m406b-acceptance",
        evidence_digests={"task_result_digest": document["content_digest"]},
    )
    worker.write_receipt_atomic(work_dir / "execution_receipt.json", receipt)
    report_path = work_dir / "RESULT" / "evidence_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(report_bytes)
    return {
        "task_result_digest": document["content_digest"],
        "report_digest": digest_bytes(report_bytes),
        "report_relative_path": "RESULT/evidence_report.json",
        "receipt_state": receipt.state,
    }


def _rebind_report(
    report: dict[str, Any], *, task_id: str, description: str, argv: Sequence[str]
) -> bytes:
    rebound = json.loads(json.dumps(report))
    rebound["report_id"] = f"report-{task_id}"
    rebound["subject"]["subject_id"] = task_id
    rebound["subject"]["description"] = description
    rebound["execution"]["argv"] = list(argv)
    return (json.dumps(rebound, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _project_task(
    *,
    work_dir: Path,
    project: str,
    task_id: str,
    release: Mapping[str, str],
    principal: str,
    research: str,
    session: str,
    report_digest: str,
) -> Any:
    from acp_services.runtime import worker
    from acp_services.runtime.evidence_projection import TaskRef, project_evidence
    from acp_services.runtime.research_session import ResearchIdentity

    receipt = worker.read_receipt(work_dir / "execution_receipt.json")
    task_result = worker.read_task_result(work_dir)
    if receipt is None or task_result is None:
        raise StepError("evidence.project", "worker artifacts are missing")
    identity = ResearchIdentity(
        principal_id=principal,
        project_id=project,
        research_id=research,
        session_id=session,
    )
    return project_evidence(
        identity=identity,
        round_index=0,
        task_ref=TaskRef(
            project_id=project,
            task_id=task_id,
            job_id=task_id,
            attempt=1,
            release_id=release["release_id"],
            release_digest=release["release_digest"],
        ),
        receipt=receipt.to_dict(),
        task_result=task_result,
        report=(work_dir / "RESULT" / "evidence_report.json").read_bytes(),
        report_digest=report_digest,
        read_root=work_dir,
    )


def _curve_product(*, series: Sequence[Any], raw_input: Any, algorithm: str) -> dict[str, Any]:
    from acp.results.frames_adapter import project_science_product
    from cccp.calculation.products.curve import build_curve_scatter_product

    product = build_curve_scatter_product(
        series=series, raw_input=raw_input, algorithm=algorithm, algorithm_version="1"
    )
    projection = project_science_product(product, view_type="scan")
    return {
        "product": product.to_dict(),
        "projection": projection,
        "product_digest": digest_bytes(
            json.dumps(product.to_dict(), sort_keys=True, ensure_ascii=False).encode()
        ),
        "projection_digest": digest_bytes(
            json.dumps(projection, sort_keys=True, ensure_ascii=False).encode()
        ),
    }


def _capability_snapshot(controller: ControllerProcess, key: str, project: str) -> dict[str, Any]:
    status, catalog = controller.request(
        "GET",
        f"/api/v2/science/capabilities?project_id={urllib.parse.quote(project)}",
        headers={"X-API-Key": key},
    )
    if status != 200 or not isinstance(catalog, Mapping):
        raise StepError("capability.catalog", f"HTTP {status}: {catalog}")
    capabilities = catalog.get("capabilities") or {}
    descriptors = capabilities.get("descriptors") or {}
    domain = capabilities.get("domain") or {}
    domain_entries = domain.get("capabilities") or []
    return {
        "http_status": status,
        "catalog_sha256": digest_bytes(
            json.dumps(catalog, sort_keys=True, ensure_ascii=False).encode()
        ),
        "descriptor_ids": sorted(
            str(entry.get("capability_id"))
            for entry in descriptors.get("capabilities") or []
            if isinstance(entry, Mapping)
        ),
        "domain_ids": sorted(
            str(entry.get("capability_id"))
            for entry in domain_entries
            if isinstance(entry, Mapping)
        ),
        "domain_version": domain.get("version"),
        "analysis_ids": sorted(
            str(entry.get("analysis_id"))
            for entry in capabilities.get("analysis") or []
            if isinstance(entry, Mapping)
        ),
    }


def _capability_forms(controller: ControllerProcess, key: str, project: str) -> dict[str, Any]:
    status, forms = controller.request(
        "GET",
        f"/api/v2/science/capability-forms?project_id={urllib.parse.quote(project)}",
        headers={"X-API-Key": key},
    )
    if status != 200 or not isinstance(forms, Mapping):
        raise StepError("capability.forms", f"HTTP {status}: {forms}")
    entries = forms.get("forms") or []
    nmr = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and str(entry.get("capability_id")) in {"nmr.shielding", "nmr.compare"}
    ]
    thermo = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and str(entry.get("capability_id")) == "thermochemistry.ensemble"
    ]
    return {
        "http_status": status,
        "subset_version": forms.get("subset_version"),
        "subset_digest": forms.get("subset_digest"),
        "forms_sha256": digest_bytes(
            json.dumps(forms, sort_keys=True, ensure_ascii=False).encode()
        ),
        "nmr_states": [entry.get("state") for entry in nmr],
        "thermo_states": [entry.get("state") for entry in thermo],
        "entry_count": len(entries),
    }


def _reserve(
    ledger: Any, budget_id: str, reservation_id: str, units: Mapping[str, int]
) -> dict[str, Any]:
    decision = ledger.reserve_research_budget(
        budget_id, units, reservation_id=reservation_id, detail="m4-06b cross-domain row"
    )
    return decision.to_dict()


def _settle(
    ledger: Any,
    budget_id: str,
    reservation_id: str,
    counters: Mapping[str, int],
    receipt_id: str,
) -> dict[str, Any]:
    from acp_services.runtime.usage_units import UsageUnitCounts

    settlement = ledger.settle_research_budget(
        budget_id,
        receipt_id=receipt_id,
        reservation_id=reservation_id,
        counters=UsageUnitCounts.from_mapping(counters),
    )
    return settlement.to_dict()


def run_nmr_row(ctx: Context, ledger: Any, *, budget_id: str) -> dict[str, Any]:
    """NMR cross-domain row: real xTB + declared stub shielding/fixture spectrum."""
    from cccp.analysis.nmr.comparison import (
        NmrAtomMapping,
        NmrExperimentalSource,
        NmrReferenceConditions,
        NmrShieldingComparisonInput,
        model_asset_record,
    )
    from cccp.analysis.nmr.models import ExperimentalPeak
    from cccp.analysis.research.evidence import Digest
    from cccp.analysis.research.provenance import ProvenanceArtifactRef
    from cccp.calculation.context import TaskContext
    from cccp.calculation.products.contracts import CurveSeries, PointRecord, SeriesRef
    from cccp.calculation.requests import (
        MethodSpec,
        OptimizeOptions,
        StructureInput,
        TaskKind,
        TaskRequest,
    )
    from cccp.calculation.tasks.optimize import run_optimize

    project = PROJECTS[0]
    task_id = "m406b-nmr-crossdomain"
    work_dir = ctx.work / "cross-domain" / "nmr"
    work_dir.mkdir(parents=True, exist_ok=True)
    key = ctx.keys[project].key
    principal = USERS[project][0]
    fixture = read_json(NMR_FIXTURE)

    capability = _capability_snapshot(ctx.controller, key, project)
    forms = _capability_forms(ctx.controller, key, project)
    reservation_id = "rbr-m406b-nmr-h1"
    # The canonical pattern: the reservation holds the TaskRef; the trusted
    # receipt settles the QC invocations that actually ran (settling a
    # reservation's own qc_invocation would count it twice).
    reservation = _reserve(ledger, budget_id, reservation_id, {"task_ref": 1})

    # ① real xTB optimisation (the only live QC in this row)
    xyz_path = work_dir / "input.xyz"
    xyz_path.write_text(ETHANOL_XYZ, encoding="utf-8")
    started = time.perf_counter()
    optimize = run_optimize(
        TaskRequest(
            task=TaskKind.OPTIMIZE,
            structure=StructureInput(path=xyz_path, role="minimum"),
            charge=0,
            multiplicity=1,
            level=MethodSpec(method="gfn2"),
            backend="xtb",
            options=OptimizeOptions(),
            output_dir=work_dir / "xtb",
        ),
        context=TaskContext(workdir=work_dir / "xtb"),
    )
    duration_s = round(time.perf_counter() - started, 2)
    if optimize.status != "completed":
        raise StepError("nmr.xtb", f"xTB optimisation did not complete: {optimize.errors}")
    xtb_artifacts = []
    for artifact in optimize.artifacts:
        if artifact.path.is_file():
            xtb_artifacts.append(
                {
                    "type": artifact.type,
                    "path": artifact.path.name,
                    "sha256": file_digest(artifact.path),
                    "bytes": artifact.path.stat().st_size,
                }
            )
    optimized_xyz = next(
        (artifact.path for artifact in optimize.artifacts if artifact.path.name.endswith(".xyz")),
        None,
    )
    if optimized_xyz is None or not optimized_xyz.is_file():
        raise StepError("nmr.xtb", "xTB returned no optimised structure")
    xyz_lines = optimized_xyz.read_text(encoding="utf-8").splitlines()
    if int(xyz_lines[0].strip()) != len(fixture["symbols"]):
        raise StepError(
            "nmr.xtb",
            f"optimised structure has {xyz_lines[0]!r} atoms, fixture mapping has "
            f"{len(fixture['symbols'])}",
        )
    xyz_symbols = [line.split()[0] for line in xyz_lines[2:] if line.split()]
    if xyz_symbols != list(fixture["symbols"]):
        raise StepError("nmr.xtb", "optimised structure symbols differ from the fixture mapping")

    # ② declared stub boundary: the GIAO shielding table and the experimental
    #    peak list are the published A-24 cross-domain fixture, not a new run.
    structure_path = work_dir / "structure.xyz"
    shutil.copyfile(optimized_xyz, structure_path)
    shielding_path = work_dir / "shielding_table.json"
    shielding_path.write_text(json.dumps(fixture["shieldings"], sort_keys=True), encoding="utf-8")
    experiment_path = work_dir / "experimental_spectrum.json"
    experiment_path.write_text(json.dumps(fixture["experiment"], sort_keys=True), encoding="utf-8")
    reference_path = work_dir / "reference_conditions.json"
    reference_path.write_text(json.dumps(fixture["reference"], sort_keys=True), encoding="utf-8")
    model_version, model_available, _hashes = model_asset_record(None)
    model_path = work_dir / "model_assets_manifest.json"
    model_path.write_text(
        json.dumps(
            {"model_asset_version": model_version, "model_assets_available": model_available},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    def ref(name: str, role: str, units: str | None = None) -> ProvenanceArtifactRef:
        return ProvenanceArtifactRef(
            relative_path=name,
            digest=Digest.from_bytes((work_dir / name).read_bytes()),
            role=role,
            units=units,
        )

    comparison_input = NmrShieldingComparisonInput(
        molecule_id=f"{task_id}-ethanol",
        mapping=NmrAtomMapping(
            tuple(fixture["symbols"]),
            tuple(fixture["atom_labels"]),
            int(fixture["atom_index_base"]),
        ),
        shieldings={int(key): value for key, value in fixture["shieldings"].items()},
        experiment=NmrExperimentalSource(
            source_ref=fixture["experiment"]["source_ref"],
            peaks=tuple(ExperimentalPeak(**peak) for peak in fixture["experiment"]["peaks"]),
            equivalence_groups=tuple(
                tuple(group) for group in fixture["experiment"]["equivalence_groups"]
            ),
        ),
        reference=NmrReferenceConditions(**fixture["reference"]),
        source_artifacts=(
            ref("shielding_table.json", "shielding_table", "ppm"),
            ref("experimental_spectrum.json", "experimental_spectrum", "ppm"),
            ref("reference_conditions.json", "reference_conditions"),
            ref("model_assets_manifest.json", "model_assets_manifest"),
            ref("structure.xyz", "structure", "angstrom"),
        ),
    )
    from cccp.calculation.capabilities import bind_capability, domain_registry

    capability_binding = bind_capability(
        "nmr.compare", comparison_input, registry=domain_registry()
    )
    binding_preflight = capability_binding.preflight()
    outcome = capability_binding.execute()
    comparison_payload = outcome.to_dict()
    comparison_path = work_dir / "RESULT" / "nmr_comparison.json"
    comparison_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(comparison_path, comparison_payload)
    from acp.storage.manifest import ProductKind, ResultManifest

    nmr_manifest = ResultManifest(task_id=task_id, workflow="nmr", status="completed")
    nmr_manifest.add_product(
        "nmr_comparison",
        "NMR shielding comparison",
        "nmr_comparison.json",
        ProductKind.REPORT,
    )
    nmr_manifest_path = nmr_manifest.write(work_dir / "RESULT")

    report_bytes = _rebind_report(
        outcome.evidence.to_dict(),
        task_id=task_id,
        description=(
            "NMR cross-domain row: real xTB (gfn2) optimised ethanol structure; the "
            "GIAO shielding table and the experimental peak list are the declared "
            "A-24 constructed fixtures (evidence_kind=declared_stub+fixture). The "
            "comparison itself is the real cccp.analysis.nmr.comparison code."
        ),
        argv=["cccp.calculation.tasks.optimize.run_optimize", "cccp.analysis.nmr.comparison"],
    )
    worker = _worker_attempt(
        work_dir=work_dir,
        task_id=task_id,
        task="nmr_shielding",
        result={
            "xtb_energy_hartree": optimize.energy_hartree,
            "nmr_status": outcome.status,
            "atom_mapping_digest": outcome.atom_mapping_digest,
            "paired_peak_count": outcome.paired_peak_count,
            "model_assets_available": outcome.model_assets_available,
        },
        report_bytes=report_bytes,
        release=ctx.release,
    )
    view = _project_task(
        work_dir=work_dir,
        project=project,
        task_id=task_id,
        release=ctx.release,
        principal=principal,
        research="res-m406b-nmr",
        session="ses-m406b-nmr",
        report_digest=worker["report_digest"],
    )
    from acp_services.runtime.evidence_projection import decide_evidence

    decision = decide_evidence(
        view, "defer"
    ).to_dict()  # the declared fixture boundary is never an "accept"

    # ③ ViewModel: one curve series per nucleus from the real xTB structure's
    #    declared shielding table -> the same frame/annotation contract.
    series = []
    for nucleus, element in (("1H", "H"), ("13C", "C")):
        points = []
        for position, (label, symbol) in enumerate(
            zip(fixture["atom_labels"], fixture["symbols"], strict=True)
        ):
            if symbol != element:
                continue
            entry = fixture["shieldings"][str(position)]
            points.append(
                PointRecord(
                    point_id=f"nmr-{label}",
                    x=float(position),
                    y=float(entry["isotropic"]),
                )
            )
        series.append(
            CurveSeries(
                ref=SeriesRef(
                    series_id=f"nmr.shielding.{nucleus}",
                    x_unit="atom_index",
                    y_unit="ppm",
                    label=f"{nucleus} isotropic shielding",
                ),
                points=tuple(points),
            )
        )
    viewmodel = _curve_product(
        series=series, raw_input=comparison_payload, algorithm="nmr.shielding_table"
    )
    viewmodel["view_type"] = "scan"
    viewmodel["node_keys"] = sorted(
        {key for node in viewmodel["projection"]["nodes"] for key in node}
    )
    viewmodel["annotation_keys"] = sorted(
        {key for row in viewmodel["projection"]["annotations"] for key in row}
    )
    settlement = _settle(
        ledger,
        budget_id,
        reservation_id,
        {"task_ref": 1, "qc_invocation": 1},
        receipt_id=f"rcpt-{task_id}",
    )
    return {
        "row": "cross_domain_nmr",
        "domain": "nmr",
        "project": project,
        "task_id": task_id,
        "evidence_kind": (
            "mixed: native_xtb_optimize + declared_stub_shielding + fixture_experiment"
        ),
        "declared_boundaries": [
            "the GIAO shielding table is the A-24 constructed fixture (no ORCA GIAO run)",
            "the experimental peak list is the A-24 constructed fixture (no spectrum acquired)",
            "the comparison and the xTB optimisation are real candidate code paths",
        ],
        "capability": capability,
        "capability_binding": {
            "capability_id": capability_binding.descriptor.capability_id,
            "implementation_id": capability_binding.descriptor.implementation_id,
            "execution_kind": capability_binding.descriptor.execution_semantics.kind,
            "selection_mode": str(capability_binding.selection.mode),
            "delegation_target": capability_binding.delegation_target,
            "preflight_ready": binding_preflight.ready,
            "registry": "domain_registry()",
        },
        "capability_forms": forms,
        "budget": {"budget_id": budget_id, "reservation": reservation, "settlement": settlement},
        "execution": {
            "xtb_status": optimize.status,
            "xtb_energy_hartree": optimize.energy_hartree,
            "duration_s": duration_s,
            "artifacts": xtb_artifacts,
        },
        "input_digests": {
            "structure.xyz": file_digest(structure_path),
            "shielding_table.json": file_digest(shielding_path),
            "experimental_spectrum.json": file_digest(experiment_path),
            "reference_conditions.json": file_digest(reference_path),
            "model_assets_manifest.json": file_digest(model_path),
            "nmr_comparison.json": file_digest(comparison_path),
        },
        "product": {
            "manifest": "RESULT/result_manifest.json",
            "manifest_sha256": file_digest(nmr_manifest_path),
            "products": [product.to_dict() for product in nmr_manifest.products],
        },
        "comparison": {
            "status": outcome.status,
            "atom_mapping_digest": outcome.atom_mapping_digest,
            "model_asset_version": outcome.model_asset_version,
            "model_assets_available": outcome.model_assets_available,
            "paired_peak_count": outcome.paired_peak_count,
            "claim_status": outcome.evidence.overall_claim_status,
            "report_id": outcome.evidence.report_id,
        },
        "worker": worker,
        "evidence_view": view.to_dict(),
        "research_decision": {
            **decision,
            "basis": "defer because the GIAO shielding table and the experimental "
            "spectrum are declared fixtures; the projection itself is complete",
        },
        "viewmodel": viewmodel,
        "digest_chain": {
            "structure": file_digest(structure_path),
            "comparison": file_digest(comparison_path),
            "report": worker["report_digest"],
            "task_result": worker["task_result_digest"],
            "projection": view.projection_digest,
            "viewmodel_product": viewmodel["product_digest"],
            "viewmodel_projection": viewmodel["projection_digest"],
        },
    }


def run_thermochemistry_row(ctx: Context, ledger: Any, *, budget_id: str) -> dict[str, Any]:
    """Thermochemistry row: real Shermo over the M4-04 route-a native inputs."""
    from cccp.analysis.ensemble.science import (
        EnsembleComponent,
        EnsembleThermochemistryInput,
    )
    from cccp.analysis.research.evidence import Digest
    from cccp.analysis.research.provenance import ProvenanceArtifactRef
    from cccp.calculation.context import TaskContext
    from cccp.calculation.products.contracts import CurveSeries, PointRecord, SeriesRef
    from cccp.calculation.requests import (
        TaskKind,
        TaskRequest,
        ThermochemistryOptions,
    )
    from cccp.calculation.tasks.thermochemistry import run_thermochemistry

    project = PROJECTS[0]
    task_id = "m406b-thermo-crossdomain"
    work_dir = ctx.work / "cross-domain" / "thermochemistry"
    work_dir.mkdir(parents=True, exist_ok=True)
    key = ctx.keys[project].key
    principal = USERS[project][0]

    capability = _capability_snapshot(ctx.controller, key, project)
    forms = _capability_forms(ctx.controller, key, project)
    reservation_id = "rbr-m406b-thermo-h1"
    reservation = _reserve(ledger, budget_id, reservation_id, {"task_ref": 1})

    chain = read_json(ROUTE_A_CHAIN / "energy_thermochemistry.json")
    sp_energy = float(chain["energies"]["transition_state"]["energy_hartree"])
    if not ROUTE_A_FREQ_LOG.is_file():
        raise StepError("thermo.input", f"route-a frequency log is missing: {ROUTE_A_FREQ_LOG}")
    route_a_freq_digest = file_digest(ROUTE_A_FREQ_LOG)
    freq_dir = work_dir / "frequency"
    freq_dir.mkdir(parents=True, exist_ok=True)
    freq_path = freq_dir / "freq.out"
    shutil.copyfile(ROUTE_A_FREQ_LOG, freq_path)
    if file_digest(freq_path) != route_a_freq_digest:
        raise StepError("thermo.input", "copied route-a frequency log digest changed")

    temperatures = (273.15, 298.15, 323.15)
    runs: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, temperature in enumerate(temperatures):
        output_dir = work_dir / "shermo" / f"t{index}"
        output_dir.mkdir(parents=True, exist_ok=True)
        result = run_thermochemistry(
            TaskRequest(
                task=TaskKind.THERMOCHEMISTRY,
                options=ThermochemistryOptions(
                    freq_log_path=freq_path,
                    sp_energy_hartree=sp_energy,
                    temperature_k=temperature,
                    pressure_atm=1.0,
                    standard_state="1atm",
                ),
                output_dir=output_dir,
            ),
            context=TaskContext(workdir=output_dir),
        )
        if result.status != "completed" or result.payload is None:
            raise StepError(
                "thermo.shermo", f"Shermo run at {temperature} K failed: {result.errors}"
            )
        artifacts = [
            {
                "type": artifact.type,
                "path": artifact.path.name,
                "sha256": file_digest(artifact.path),
                "bytes": artifact.path.stat().st_size,
            }
            for artifact in result.artifacts
            if artifact.path.is_file()
        ]
        runs.append(
            {
                "temperature_k": temperature,
                "gibbs_hartree": result.payload.gibbs_hartree,
                "enthalpy_hartree": result.payload.enthalpy_hartree,
                "entropy_au": result.payload.entropy_au,
                "gibbs_source": result.payload.gibbs_source,
                "standard_state": result.payload.standard_state,
                "artifacts": artifacts,
            }
        )
    duration_s = round(time.perf_counter() - started, 2)
    reference_run = next(run for run in runs if run["temperature_k"] == 298.15)

    component_path = work_dir / "component_energy_table.json"
    write_json(
        component_path,
        {
            "components": [
                {
                    "component_id": "route-a-ts",
                    "gibbs_hartree": reference_run["gibbs_hartree"],
                    "weight": 1.0,
                    "source": f"shermo:{reference_run['artifacts'][0]['sha256']}",
                }
            ],
            "standard_state": "1atm",
            "temperature_kelvin": 298.15,
            "energy_kind": "gibbs_free",
            "source": "M4-04 route-a ts_frequency native log + real Shermo 2.6.1",
        },
    )
    ensemble_input = EnsembleThermochemistryInput(
        ensemble_id=f"{task_id}-ensemble",
        components=(
            EnsembleComponent(
                component_id="route-a-ts",
                gibbs_hartree=reference_run["gibbs_hartree"],
                weight=1.0,
                source="shermo",
            ),
        ),
        temperature_kelvin=298.15,
        weights_mode="raw",
        standard_state="1atm",
        source_artifacts=(
            ProvenanceArtifactRef(
                relative_path="component_energy_table.json",
                digest=Digest.from_bytes(component_path.read_bytes()),
                role="component_energy_table",
                units="hartree",
            ),
        ),
    )
    from cccp.calculation.capabilities import bind_capability, domain_registry

    capability_binding = bind_capability(
        "thermochemistry.ensemble", ensemble_input, registry=domain_registry()
    )
    binding_preflight = capability_binding.preflight()
    ensemble = capability_binding.execute()
    ensemble_payload = ensemble.to_dict()
    ensemble_path = work_dir / "RESULT" / "thermochemistry_ensemble.json"
    ensemble_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(ensemble_path, ensemble_payload)
    thermo_json = work_dir / "RESULT" / "thermo.json"
    write_json(
        thermo_json,
        {
            "sp_energy_hartree": sp_energy,
            "thermal_correction_u_hartree": (reference_run["enthalpy_hartree"] - sp_energy),
            "total_enthalpy_hartree": reference_run["enthalpy_hartree"],
            "total_gibbs_hartree": reference_run["gibbs_hartree"],
            "entropy": reference_run["entropy_au"],
            "temperature_k": reference_run["temperature_k"],
        },
    )
    from acp.results.thermochemistry import build_thermo_report
    from acp.storage.manifest import ProductKind, ResultManifest

    thermo_view = build_thermo_report(thermo_json)
    thermo_manifest = ResultManifest(
        task_id=task_id, workflow="thermochemistry", status="completed"
    )
    thermo_manifest.add_product(
        "thermo_report", "Thermochemistry report", "thermo.json", ProductKind.THERMO_REPORT
    )
    thermo_manifest.add_product(
        "thermo_ensemble",
        "Ensemble thermochemistry",
        "thermochemistry_ensemble.json",
        ProductKind.REPORT,
    )
    thermo_manifest_path = thermo_manifest.write(work_dir / "RESULT")

    report_bytes = _rebind_report(
        ensemble.evidence.to_dict(),
        task_id=task_id,
        description=(
            "Thermochemistry cross-domain row: real Shermo 2.6.1 thermochemistry "
            "recomputed from the M4-04 route-a endpoint TS frequency log and its "
            "native single-point energy (digest-verified); the ensemble analysis "
            "is the real cccp.analysis.ensemble implementation."
        ),
        argv=["cccp.calculation.tasks.thermochemistry.run_thermochemistry"],
    )
    worker = _worker_attempt(
        work_dir=work_dir,
        task_id=task_id,
        task="thermochemistry",
        result={
            "gibbs_hartree": reference_run["gibbs_hartree"],
            "enthalpy_hartree": reference_run["enthalpy_hartree"],
            "entropy_au": reference_run["entropy_au"],
            "standard_state": "1atm",
            "temperature_k": 298.15,
            "ensemble_status": ensemble.status,
        },
        report_bytes=report_bytes,
        release=ctx.release,
    )
    view = _project_task(
        work_dir=work_dir,
        project=project,
        task_id=task_id,
        release=ctx.release,
        principal=principal,
        research="res-m406b-thermo",
        session="ses-m406b-thermo",
        report_digest=worker["report_digest"],
    )
    from acp_services.runtime.evidence_projection import decide_evidence

    decision = decide_evidence(view, "accept").to_dict()

    series = [
        CurveSeries(
            ref=SeriesRef(
                series_id="thermochemistry.gibbs.vs.temperature",
                x_unit="kelvin",
                y_unit="hartree",
                label="Gibbs free energy vs temperature",
            ),
            points=tuple(
                PointRecord(
                    point_id=f"thermo-t{index}",
                    x=float(run["temperature_k"]),
                    y=float(run["gibbs_hartree"]),
                )
                for index, run in enumerate(runs)
            ),
        )
    ]
    viewmodel = _curve_product(
        series=series, raw_input=ensemble_payload, algorithm="thermochemistry.gibbs_curve"
    )
    viewmodel["view_type"] = "scan"
    viewmodel["node_keys"] = sorted(
        {key for node in viewmodel["projection"]["nodes"] for key in node}
    )
    viewmodel["annotation_keys"] = sorted(
        {key for row in viewmodel["projection"]["annotations"] for key in row}
    )
    settlement = _settle(
        ledger,
        budget_id,
        reservation_id,
        {"task_ref": 1, "qc_invocation": len(runs)},
        receipt_id=f"rcpt-{task_id}",
    )
    return {
        "row": "cross_domain_thermochemistry",
        "domain": "thermochemistry",
        "project": project,
        "task_id": task_id,
        "evidence_kind": ("native: M4-04 route-a inputs (digest-verified) + real Shermo 2.6.1"),
        "declared_boundaries": [
            "the frequency log and the single-point energy are the M4-04 route-a native "
            "artifacts; this row does not recompute them",
            "the ensemble weights are supplied (single component, weight 1.0) and "
            "recorded, never re-derived",
        ],
        "capability": capability,
        "capability_binding": {
            "capability_id": capability_binding.descriptor.capability_id,
            "implementation_id": capability_binding.descriptor.implementation_id,
            "execution_kind": capability_binding.descriptor.execution_semantics.kind,
            "selection_mode": str(capability_binding.selection.mode),
            "delegation_target": capability_binding.delegation_target,
            "preflight_ready": binding_preflight.ready,
            "registry": "domain_registry()",
        },
        "capability_forms": forms,
        "budget": {"budget_id": budget_id, "reservation": reservation, "settlement": settlement},
        "execution": {
            "duration_s": duration_s,
            "route_a_freq_log": str(ROUTE_A_FREQ_LOG),
            "route_a_freq_log_sha256": route_a_freq_digest,
            "sp_energy_hartree": sp_energy,
            "runs": runs,
        },
        "input_digests": {
            "frequency/freq.out": file_digest(freq_path),
            "component_energy_table.json": file_digest(component_path),
            "thermochemistry_ensemble.json": file_digest(ensemble_path),
        },
        "product": {
            "manifest": "RESULT/result_manifest.json",
            "manifest_sha256": file_digest(thermo_manifest_path),
            "thermo_view": thermo_view,
            "products": [product.to_dict() for product in thermo_manifest.products],
        },
        "ensemble": {
            "status": ensemble.status,
            "claim_status": ensemble.evidence.overall_claim_status,
            "report_id": ensemble.evidence.report_id,
            "total_gibbs_hartree": getattr(ensemble, "total_gibbs_hartree", None),
        },
        "worker": worker,
        "evidence_view": view.to_dict(),
        "research_decision": {
            **decision,
            "basis": "defer because the GIAO shielding table and the experimental "
            "spectrum are declared fixtures; the projection itself is complete",
        },
        "viewmodel": viewmodel,
        "digest_chain": {
            "route_a_frequency": route_a_freq_digest,
            "ensemble_input": file_digest(component_path),
            "ensemble": file_digest(ensemble_path),
            "report": worker["report_digest"],
            "task_result": worker["task_result_digest"],
            "projection": view.projection_digest,
            "viewmodel_product": viewmodel["product_digest"],
            "viewmodel_projection": viewmodel["projection_digest"],
        },
    }


def cross_domain_reuse(nmr: Mapping[str, Any], thermo: Mapping[str, Any]) -> dict[str, Any]:
    """The four reuse assertions H6 names: capability, budget, evidence, ViewModel."""
    cap_ok = (
        nmr["capability"]["catalog_sha256"] == thermo["capability"]["catalog_sha256"]
        and "nmr.shielding" in nmr["capability"]["domain_ids"]
        and "thermochemistry.ensemble" in thermo["capability"]["domain_ids"]
        and nmr["capability"]["domain_ids"] == thermo["capability"]["domain_ids"]
    )
    # The served form document is the frozen built-in subset; the NMR and
    # thermochemistry capabilities are domain descriptors and have no
    # ui-subset form of their own (recorded as a limitation, not a pass).
    binding_ok = (
        nmr["capability_binding"]["preflight_ready"] is True
        and thermo["capability_binding"]["preflight_ready"] is True
        and nmr["capability_binding"]["registry"] == "domain_registry()"
        and thermo["capability_binding"]["registry"] == "domain_registry()"
        and nmr["capability_binding"]["implementation_id"]
        != thermo["capability_binding"]["implementation_id"]
    )
    forms_ok = (
        nmr["capability_forms"]["subset_digest"] == thermo["capability_forms"]["subset_digest"]
        and nmr["capability_forms"]["forms_sha256"] == thermo["capability_forms"]["forms_sha256"]
        and nmr["capability_forms"]["entry_count"] == thermo["capability_forms"]["entry_count"]
    )
    budget_ok = (
        nmr["budget"]["budget_id"] == thermo["budget"]["budget_id"]
        and nmr["budget"]["settlement"]["applied"] is True
        and thermo["budget"]["settlement"]["applied"] is True
    )
    evidence_ok = (
        nmr["evidence_view"]["schema"] == thermo["evidence_view"]["schema"]
        and nmr["evidence_view"]["evidence_status"] == thermo["evidence_view"]["evidence_status"]
        and nmr["research_decision"]["schema"] == thermo["research_decision"]["schema"]
        and nmr["worker"]["report_digest"] != thermo["worker"]["report_digest"]
    )
    viewmodel_ok = (
        nmr["viewmodel"]["projection"]["view_type"]
        == thermo["viewmodel"]["projection"]["view_type"]
        and nmr["viewmodel"]["node_keys"] == thermo["viewmodel"]["node_keys"]
        and nmr["viewmodel"]["annotation_keys"] == thermo["viewmodel"]["annotation_keys"]
        and bool(nmr["viewmodel"]["projection"]["nodes"])
        and bool(thermo["viewmodel"]["projection"]["nodes"])
        and nmr["viewmodel"]["product_digest"] != thermo["viewmodel"]["product_digest"]
    )
    return {
        "capability": {
            "passed": cap_ok,
            "catalog_sha256": nmr["capability"]["catalog_sha256"],
            "domain_ids": nmr["capability"]["domain_ids"],
            "binding": {
                "passed": binding_ok,
                "nmr": nmr["capability_binding"],
                "thermochemistry": thermo["capability_binding"],
            },
            "forms": {
                "passed": forms_ok,
                "subset_digest": nmr["capability_forms"]["subset_digest"],
                "forms_sha256": nmr["capability_forms"]["forms_sha256"],
            },
        },
        "budget": {
            "passed": budget_ok,
            "budget_id": nmr["budget"]["budget_id"],
            "nmr_remaining": nmr["budget"]["settlement"].get("remaining"),
            "thermo_remaining": thermo["budget"]["settlement"].get("remaining"),
            "same_ledger": True,
        },
        "evidence": {
            "passed": evidence_ok,
            "projection_schema": nmr["evidence_view"]["schema"],
            "nmr_projection_digest": nmr["evidence_view"]["projection_digest"],
            "thermo_projection_digest": thermo["evidence_view"]["projection_digest"],
            "same_contract": True,
        },
        "viewmodel": {
            "passed": viewmodel_ok,
            "view_type": nmr["viewmodel"]["projection"]["view_type"],
            "node_keys": nmr["viewmodel"]["node_keys"],
            "annotation_keys": nmr["viewmodel"]["annotation_keys"],
            "nmr_product": nmr["viewmodel"]["product_digest"],
            "thermo_product": thermo["viewmodel"]["product_digest"],
        },
        "passed": all((cap_ok, binding_ok, forms_ok, budget_ok, evidence_ok, viewmodel_ok)),
    }


def copy_cross_domain_evidence(ctx: Context) -> dict[str, Any]:
    """Archive the small native artifacts behind the digest chain.

    Only the cross-domain work trees are copied (xTB/Shermo logs, report bytes,
    worker artifacts); the Controller DBs and the seeded job trees stay in the
    isolated run root.  Every copied file is recorded with its SHA-256 so a
    reviewer can re-verify the digest chain without re-running QC.
    """
    source = ctx.work / "cross-domain"
    target = ctx.out / "artifacts" / "cross-domain"
    files: dict[str, str] = {}
    copied = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        files[f"cross-domain/{relative}"] = file_digest(destination)
        copied += 1
    # The per-project frozen plugin snapshots and the shared-relative-path
    # artifact are the isolation evidence, so archive their exact bytes too.
    for project in PROJECTS:
        task = ctx.records["seeded_tasks"][project]
        work_dir = Path(task["work_dir"])
        for source_file, name in (
            (
                work_dir / "WORK" / "00_RUNTIME" / "frozen" / "plugin_snapshot" / "freeze.json",
                f"isolation/{project}/plugin_snapshot/freeze.json",
            ),
            (
                work_dir / "RESULT" / "native_artifact.txt",
                f"isolation/{project}/native_artifact.txt",
            ),
        ):
            destination = ctx.out / "artifacts" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
            files[name] = file_digest(destination)
            copied += 1
    return {"root": "artifacts", "files": copied, "digests": files}


# ---------------------------------------------------------------------------
# Multi-tenant / multi-hypothesis matrix
# ---------------------------------------------------------------------------


def research_memory_matrix(ctx: Context) -> dict[str, Any]:
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        ResearchResourceAuthorizationError,
        ResearchResourceConflictError,
        ResearchResourceRequest,
        ResearchResourceStore,
        research_resource_key,
    )

    store = ResearchResourceStore(ctx.resources_db)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    def create(project: str, hypothesis: str) -> dict[str, Any]:
        principal = USERS[project][0]
        request = ResearchResourceRequest(
            resource_id="",
            schema=PROPOSAL_SCHEMA,
            document={
                "schema": PROPOSAL_SCHEMA,
                "proposal_id": f"proposal.{project}.{hypothesis}",
                "project_id": project,
                "hypothesis": hypothesis,
                "question": f"M4-06b concurrent hypothesis {hypothesis}",
            },
            principal_id=principal,
            project_id=project,
        )
        record = store.create(request)
        return record.to_dict()

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(create, project, hypothesis): (project, hypothesis)
            for project in PROJECTS
            for hypothesis in HYPOTHESES
        }
        for future in concurrent.futures.as_completed(futures):
            project, hypothesis = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:  # noqa: BLE001 - acceptance records the failure
                errors.append(f"{project}/{hypothesis}: {type(exc).__name__}: {exc}")
    if errors:
        raise StepError("research_memory.create", "; ".join(errors))

    # Same document id in another project is a *different* resource; CAS in one
    # project is invisible to the other.
    alpha_doc = "proposal.project_m406b_alpha.hypothesis.h1"
    beta_doc = "proposal.project_m406b_beta.hypothesis.h1"
    alpha_key = research_resource_key(PROJECTS[0], "proposal", alpha_doc)
    beta_key = research_resource_key(PROJECTS[1], "proposal", beta_doc)
    assert alpha_key != beta_key

    cas_alpha = store.compare_and_swap(
        alpha_key,
        expected_revision=1,
        document={
            "schema": PROPOSAL_SCHEMA,
            "proposal_id": alpha_doc,
            "project_id": PROJECTS[0],
            "hypothesis": HYPOTHESES[0],
            "question": "revision 2 by alpha",
        },
        principal_id=USERS[PROJECTS[0]][0],
        project_id=PROJECTS[0],
    )
    cross_reads: list[dict[str, Any]] = []
    for project in PROJECTS:
        for hypothesis in HYPOTHESES:
            doc_id = f"proposal.{project}.{hypothesis}"
            key = research_resource_key(project, "proposal", doc_id)
            own = store.read(key, principal_id=USERS[project][0], project_id=project)
            other_project = next(item for item in PROJECTS if item != project)
            try:
                store.read(key, principal_id=USERS[other_project][0], project_id=other_project)
                cross_reads.append({"resource": key, "denied": False})
            except ResearchResourceAuthorizationError as exc:
                cross_reads.append({"resource": key, "denied": True, "reason": str(exc)})
            if own.project_id != project:
                raise StepError("research_memory.read", f"own read crossed projects: {key}")
    cross_cas_denied = False
    try:
        store.compare_and_swap(
            alpha_key,
            expected_revision=2,
            document={
                "schema": PROPOSAL_SCHEMA,
                "proposal_id": alpha_doc,
                "project_id": PROJECTS[0],
                "hypothesis": HYPOTHESES[0],
                "question": "cross-project CAS attempt",
            },
            principal_id=USERS[PROJECTS[1]][0],
            project_id=PROJECTS[1],
        )
    except ResearchResourceAuthorizationError:
        cross_cas_denied = True
    except ResearchResourceConflictError:
        cross_cas_denied = False

    heads = {
        project: len(store.list_heads(project_id=project, schema=PROPOSAL_SCHEMA))
        for project in PROJECTS
    }
    store.close()
    denied = [row for row in cross_reads if row["denied"]]
    passed = (
        len(rows) == 6
        and cas_alpha.revision == 2
        and len(denied) == 6
        and cross_cas_denied
        and heads == {project: 2 for project in PROJECTS}
    )
    return {
        "passed": passed,
        "created": len(rows),
        "projects": list(PROJECTS),
        "users": {project: USERS[project][0] for project in PROJECTS},
        "hypotheses_per_project": list(HYPOTHESES),
        "revisions": {row["project_id"]: row["revision"] for row in rows},
        "cas_revision": cas_alpha.revision,
        "cross_project_reads": cross_reads,
        "cross_project_cas_denied": cross_cas_denied,
        "heads_per_project": heads,
        "distinct_resource_ids": len({row["resource_id"] for row in rows}),
    }


def approval_matrix(ctx: Context) -> dict[str, Any]:
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.runtime.canonical_digest import canonical_digest

    service = ApprovalService(ctx.jobs_db)
    approvals: dict[str, Any] = {}

    def one(project: str, hypothesis: str) -> tuple[str, dict[str, Any]]:
        principal = USERS[project][0]
        plan = {
            "workflow": "nmr" if hypothesis == HYPOTHESES[0] else "BatchOptimize",
            "project_id": project,
            "hypothesis": hypothesis,
        }
        plan_digest = canonical_digest(plan)
        proposal_digest = canonical_digest({"proposal": project, "hypothesis": hypothesis})
        approval, _replayed = service.request(
            proposal_ref=f"proposal.{project}.{hypothesis}",
            proposal_digest=proposal_digest,
            plan_digest=plan_digest,
            project_id=project,
            requester_principal=principal,
            plan_definition=plan,
            budget={"units": {"task_ref": 2, "qc_invocation": 2}},
        )
        approved, _again = service.approve(
            approval.approval_id,
            approver_principal=principal,
            expected_revision=approval.revision,
        )
        return approval.approval_id, {
            "project": project,
            "hypothesis": hypothesis,
            "state": approved.state,
            "revision": approved.revision,
            "plan_digest": plan_digest,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(one, project, hypothesis)
            for project in PROJECTS
            for hypothesis in HYPOTHESES
        ]
        for future in concurrent.futures.as_completed(futures):
            approval_id, row = future.result()
            approvals[approval_id] = row
    cross: list[dict[str, Any]] = []
    for project in PROJECTS:
        rows = service.query(project_id=project)
        for row in rows:
            if row.project_id != project:
                cross.append({"project": project, "approval": row.approval_id, "leak": True})
        foreign_id = next(
            (approval_id for approval_id, row in approvals.items() if row["project"] != project),
            "",
        )
        foreign = service.query(project_id=project, approval_ref=foreign_id) if foreign_id else []
        if foreign:
            cross.append({"project": project, "leak": "foreign approval_ref resolved"})
    visible = {project: len(service.query(project_id=project)) for project in PROJECTS}
    passed = (
        len(approvals) == 6
        and all(row["state"] == "approved" for row in approvals.values())
        and not cross
        and visible == {project: 2 for project in PROJECTS}
    )
    return {
        "passed": passed,
        "approved": len(approvals),
        "approvals": approvals,
        "visible_per_project": visible,
        "leaks": cross,
        "query_is_project_scoped": not cross,
        "concurrent": True,
    }


def budget_matrix(ledger: Any, policies: Mapping[str, Any]) -> dict[str, Any]:
    from acp_services.runtime.research_budget import budget_id_for, parse_budget_policy
    from acp_services.runtime.usage_units import UsageUnitCounts

    budgets = {
        (project, hypothesis): budget_id_for(project, hypothesis)
        for project in PROJECTS
        for hypothesis in HYPOTHESES
    }
    before = {key: ledger.research_budget_projection(budget) for key, budget in budgets.items()}

    def reserve(project: str, hypothesis: str, budget: str, reservation: str) -> Any:
        del project, hypothesis
        return ledger.reserve_research_budget(budget, {"task_ref": 1}, reservation_id=reservation)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(
                reserve,
                project,
                hypothesis,
                budgets[(project, hypothesis)],
                f"rbr-m406b-matrix-{project}-{hypothesis.replace('.', '-')}",
            )
            for project in PROJECTS
            for hypothesis in HYPOTHESES
        ]
        decisions = [future.result() for future in futures]
    after = {key: ledger.research_budget_projection(budget) for key, budget in budgets.items()}
    deltas = {
        f"{project}/{hypothesis}": (
            after[(project, hypothesis)].consumed.get("task_ref", 0)
            - before[(project, hypothesis)].consumed.get("task_ref", 0)
            if after[(project, hypothesis)] is not None
            and before[(project, hypothesis)] is not None
            else None
        )
        for project in PROJECTS
        for hypothesis in HYPOTHESES
    }
    # A reservation id from project alpha cannot settle project beta's budget.
    transfer = ledger.settle_research_budget(
        budgets[(PROJECTS[1], HYPOTHESES[0])],
        receipt_id="rcpt-cross-budget",
        reservation_id=f"rbr-m406b-matrix-{PROJECTS[0]}-{HYPOTHESES[0].replace('.', '-')}",
        counters=UsageUnitCounts(task_ref=1),
    )
    tiny_budget = budget_id_for(PROJECTS[2], "hypothesis.budget-tiny")
    ledger.define_research_budget(
        parse_budget_policy(
            {"units": {"task_ref": 1}},
            budget_id=tiny_budget,
            project_id=PROJECTS[2],
        )
    )
    first = ledger.reserve_research_budget(
        tiny_budget, {"task_ref": 1}, reservation_id="rbr-m406b-tiny-1"
    )
    second = ledger.reserve_research_budget(
        tiny_budget, {"task_ref": 1}, reservation_id="rbr-m406b-tiny-2"
    )
    passed = (
        all(decision.admitted for decision in decisions)
        and all(delta == 1 for delta in deltas.values())
        and len(set(budgets.values())) == 6
        and transfer.applied is False
        and first.admitted is True
        and second.admitted is False
    )
    return {
        "passed": passed,
        "budgets": {f"{key[0]}/{key[1]}": value for key, value in budgets.items()},
        "distinct_budget_ids": len(set(budgets.values())) == 6,
        "consumed_before": {
            f"{key[0]}/{key[1]}": before[key].consumed if before[key] else None for key in budgets
        },
        "consumed_after": {
            f"{key[0]}/{key[1]}": after[key].consumed if after[key] else None for key in budgets
        },
        "task_ref_deltas": deltas,
        "concurrent_reservations": len(decisions),
        "cross_budget_settlement": transfer.to_dict(),
        "tiny_budget": {"first": first.to_dict(), "second": second.to_dict()},
        "policies": len(policies),
        "multi_project_budgets": len(policies),
    }


def taskref_matrix(ctx: Context, nmr_row: Mapping[str, Any]) -> dict[str, Any]:
    from acp_services.runtime.evidence_projection import (
        EvidenceProjectionError,
        TaskRef,
        parse_task_ref,
    )

    submission = nmr_row["evidence_view"]["task_ref"]
    alpha_ref = parse_task_ref(submission)
    beta_ref = TaskRef(
        project_id=PROJECTS[1],
        task_id=submission["task_id"],
        job_id=submission["task_id"],
        attempt=1,
        release_id=submission["release"]["release_id"],
        release_digest=submission["release"]["release_digest"],
    )
    foreign_refused = False
    detail = ""
    try:
        from acp_services.runtime.research_session import ResearchIdentity

        identity = ResearchIdentity(
            principal_id=USERS[PROJECTS[1]][0],
            project_id=PROJECTS[1],
            research_id="res-m406b-cross",
            session_id="ses-m406b-cross",
        )
        from acp_services.runtime.evidence_projection import project_evidence

        # The projection refuses on the project check before any file is read.
        project_evidence(
            identity=identity,
            round_index=0,
            task_ref=alpha_ref,
            receipt={},
            task_result={},
            report=b"{}",
            report_digest="sha256:" + "0" * 64,
            read_root=ctx.work,
        )
    except EvidenceProjectionError as exc:
        foreign_refused = exc.code == "foreign_project"
        detail = exc.code
    except Exception as exc:  # noqa: BLE001 - a non-typed error is also a refusal
        detail = f"{type(exc).__name__}: {exc}"
    passed = (
        alpha_ref.project_id == PROJECTS[0]
        and alpha_ref.task_id == beta_ref.task_id
        and alpha_ref.project_id != beta_ref.project_id
        and foreign_refused
    )
    return {
        "passed": passed,
        "alpha_task_ref": alpha_ref.to_dict(),
        "beta_task_ref": beta_ref.to_dict(),
        "same_task_id_different_project": alpha_ref.task_id == beta_ref.task_id,
        "foreign_project_refused": foreign_refused,
        "refusal": detail,
    }


def artifact_matrix(ctx: Context) -> dict[str, Any]:
    client = ctx.controller
    assert client is not None
    observations: list[dict[str, Any]] = []
    passed = True
    for project in PROJECTS:
        task = ctx.records["seeded_tasks"][project]
        key = ctx.keys[project].key
        own_status, own_bytes = client.download(
            f"/api/v1/jobs/{task['task_id']}/files/RESULT/native_artifact.txt",
            headers={"X-API-Key": key},
        )
        own_digest = digest_bytes(own_bytes) if own_status == 200 else ""
        if own_digest != task["artifact_sha256"]:
            passed = False
        for other in PROJECTS:
            if other == project:
                continue
            cross_status, cross_bytes = client.download(
                f"/api/v1/jobs/{task['task_id']}/files/RESULT/native_artifact.txt",
                headers={"X-API-Key": ctx.keys[other].key},
            )
            prepare_status, _ = client.request(
                "POST",
                f"/api/v1/jobs/{task['task_id']}/prepare",
                {"purpose": "artifact", "artifact_id": "manifest"},
                headers={"X-API-Key": ctx.keys[other].key},
            )
            row = {
                "project": project,
                "reader": other,
                "own_status": own_status,
                "cross_status": cross_status,
                "prepare_status": prepare_status,
                "cross_body_bytes": len(cross_bytes),
                "own_expected_sha256": task["artifact_sha256"],
                "own_served_digest": own_digest,
            }
            observations.append(row)
            if cross_status != 404 or prepare_status != 404:
                passed = False
            if cross_bytes and digest_bytes(cross_bytes) == task["artifact_sha256"]:
                passed = False
    return {
        "passed": passed,
        "observations": observations,
        "same_relative_path": "RESULT/native_artifact.txt",
        "distinct_bytes": len({row["own_expected_sha256"] for row in observations}) > 1,
    }


def event_matrix(ctx: Context) -> dict[str, Any]:
    from acp.api.routes import _ReauthorizingStreamAuthorizer
    from acp_services.contracts.events import EventScope
    from acp_services.runtime.events import AuthorizedEventStream, AuthorizedStreamRegistry
    from acp_services.store.store import JobStore

    # Rebuild the same server-side boundary the Controller uses (same env + DB).
    from acp_services.workspace.http_identity import MultiUserIdentity

    identity_boundary = MultiUserIdentity.from_environment(ctx.run_root)
    store = JobStore(ctx.jobs_db)

    class _ManagerPort:
        def get(self, job_id: str) -> Any:
            return store.get(job_id)

    registry = AuthorizedStreamRegistry()
    logs = {}
    for project in PROJECTS:
        task = ctx.records["seeded_tasks"][project]
        from acp.scheduler.events import JobEventLog
        from acp_services.store.layout import runtime_file

        work_dir = Path(task["work_dir"])
        logs[task["task_id"]] = JobEventLog(runtime_file(work_dir, "events.jsonl"))

    def log_for(job_id: str) -> Any:
        return logs.get(job_id)

    alpha_task = ctx.records["seeded_tasks"][PROJECTS[0]]["task_id"]
    beta_task = ctx.records["seeded_tasks"][PROJECTS[1]]["task_id"]
    alpha_key = ctx.keys[PROJECTS[0]].key
    identity, denial = identity_boundary.resolve(alpha_key)
    assert identity is not None and denial is None
    stream = AuthorizedEventStream(
        principal_id=identity.principal.principal_id,
        scope=EventScope.TASK,
        service_epoch="m406b",
        log_for=log_for,
    )
    stream.subscribe([alpha_task])
    guard = _ReauthorizingStreamAuthorizer(identity_boundary, alpha_key, _ManagerPort())
    authorized = stream.check_authorization(guard, job_ids=[alpha_task])
    batch = stream.poll([])
    own_events = sorted({event.owner_id for event in batch.events})
    own_event_types = sorted({event.event_type for event in batch.events})
    cross_authorized = stream.check_authorization(guard, job_ids=[beta_task])
    cross_closed = stream.closed
    cross_reason = stream.closed_reason
    registry.close_all("m406b")
    passed = (
        authorized is True
        and own_events
        and set(own_events) == {alpha_task}
        and cross_authorized is False
        and cross_closed
        and cross_reason == "permission_changed"
    )
    return {
        "passed": passed,
        "own_events": own_events,
        "own_event_types": own_event_types,
        "own_task": alpha_task,
        "cross_task": beta_task,
        "cross_authorized": cross_authorized,
        "stream_closed": cross_closed,
        "closed_reason": cross_reason,
    }


def capability_scope_matrix(ctx: Context) -> dict[str, Any]:
    client = ctx.controller
    assert client is not None
    forms = {}
    for project in PROJECTS:
        forms[project] = _capability_forms(client, ctx.keys[project].key, project)
    cross_statuses = {}
    for project in PROJECTS:
        for other in PROJECTS:
            if project == other:
                continue
            status, _ = client.request(
                "GET",
                f"/api/v2/science/capability-forms?project_id={urllib.parse.quote(other)}",
                headers={"X-API-Key": ctx.keys[project].key},
            )
            cross_statuses[f"{project}->{other}"] = status
    plugin = {}
    for project in PROJECTS:
        task = ctx.records["seeded_tasks"][project]
        own_status, _ = client.request(
            "GET",
            f"/api/v1/jobs/{task['task_id']}/files/WORK/00_RUNTIME/frozen/"
            "plugin_snapshot/descriptor.json",
            headers={"X-API-Key": ctx.keys[project].key},
        )
        other = next(item for item in PROJECTS if item != project)
        cross_status, _ = client.request(
            "GET",
            f"/api/v1/jobs/{task['task_id']}/files/WORK/00_RUNTIME/frozen/"
            "plugin_snapshot/descriptor.json",
            headers={"X-API-Key": ctx.keys[other].key},
        )
        plugin[project] = {
            "own_status": own_status,
            "cross_status": cross_status,
            "sha256": task["plugin_snapshot_sha256"],
            "freeze_sha256": task["plugin_freeze_sha256"],
        }
    passed = (
        len({row["subset_digest"] for row in forms.values()}) == 1
        and len({row["forms_sha256"] for row in forms.values()}) == 1
        and all(status == 404 for status in cross_statuses.values())
        and all(row["own_status"] == 200 for row in plugin.values())
        and all(row["cross_status"] == 404 for row in plugin.values())
        and len({row["freeze_sha256"] for row in plugin.values()}) == len(PROJECTS)
    )
    cache_passed = len(forms) == len(PROJECTS) and all(
        status == 404 for status in cross_statuses.values()
    )
    return {
        "passed": passed,
        "cache": {
            "passed": cache_passed,
            "cached_catalog_warmed_by": len(forms),
            "cross_read_after_warm_status": sorted(set(cross_statuses.values())),
            "note": (
                "the science catalog is one process cache; every read still re-authorizes "
                "the project, so a warm cache never widens the caller's scope"
            ),
        },
        "forms_per_project": forms,
        "cross_project_status": cross_statuses,
        "plugin_snapshot_per_project": plugin,
        "shared_catalog_same_bytes": len({row["forms_sha256"] for row in forms.values()}) == 1,
        "scope_enforced": all(status == 404 for status in cross_statuses.values()),
    }


# ---------------------------------------------------------------------------
# Revocation: HTTP / MCP / CLI
# ---------------------------------------------------------------------------


def revocation_three_face(ctx: Context) -> dict[str, Any]:
    client = ctx.controller
    assert client is not None
    project = PROJECTS[0]
    task_id = ctx.records["seeded_tasks"][project]["task_id"]
    key_record = ctx.keys[project]
    key = key_record.key
    mcp = McpProcess(ctx.run_root, client.base_url, api_key=key)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    try:
        mcp.initialize()
        before["http_identity"] = client.request(
            "GET", "/api/identity", headers={"X-API-Key": key}
        )[0]
        before["http_task"] = client.request(
            "GET", f"/api/v1/jobs/{task_id}", headers={"X-API-Key": key}
        )[0]
        mcp_status = mcp.tool("acp_status", {"reference": task_id})
        before["mcp_status_is_error"] = mcp_status.get("isError") is True
        before["mcp_task_id"] = str(mcp_data(mcp_status).get("task_id") or "")
        cli = run_cli(
            ["task", "query", "--controller", client.base_url, "--job", task_id],
            env_extra={"ACP_API_KEY": key},
        )
        before["cli"] = {
            "returncode": cli["returncode"],
            "status": (cli["payload"] or {}).get("status"),
            "code": (cli["payload"] or {}).get("code"),
        }
        revoked = ctx.key_manager.revoke(key_record.key_id)
        after["http_identity"] = client.request("GET", "/api/identity", headers={"X-API-Key": key})[
            0
        ]
        after["http_task"] = client.request(
            "GET", f"/api/v1/jobs/{task_id}", headers={"X-API-Key": key}
        )[0]
        mcp_after = mcp.tool("acp_status", {"reference": task_id})
        after["mcp_is_error"] = mcp_after.get("isError") is True
        after["mcp_error_code"] = mcp_error_code(mcp_after)
        cli_after = run_cli(
            ["task", "query", "--controller", client.base_url, "--job", task_id],
            env_extra={"ACP_API_KEY": key},
        )
        after["cli"] = {
            "returncode": cli_after["returncode"],
            "status": (cli_after["payload"] or {}).get("status"),
            "code": (cli_after["payload"] or {}).get("code"),
        }
    finally:
        mcp.close()
    passed = (
        revoked is True
        and before["http_identity"] == 200
        and before["http_task"] == 200
        and before["mcp_status_is_error"] is False
        and before["cli"]["returncode"] == 0
        and after["http_identity"] == 401
        and after["http_task"] == 401
        and after["mcp_is_error"] is True
        and after["mcp_error_code"] == "access_denied"
        and after["cli"]["returncode"] != 0
        and after["cli"]["code"] in {"unauthenticated", "access_denied"}
    )
    return {
        "passed": passed,
        "project": project,
        "task_id": task_id,
        "key_id": key_record.key_id,
        "revoked": revoked,
        "before": before,
        "after": after,
        "faces": ["http", "mcp_stdio", "cli_task_query_controller"],
        "credential_surface": "ACP_API_KEY (same env for mcp serve and task --controller)",
    }


# ---------------------------------------------------------------------------
# H6 input
# ---------------------------------------------------------------------------


def h4_snapshot() -> dict[str, Any]:
    """Report H4 unchanged by this task (M4-04 route-a remains its feed)."""
    path = REPO / "reports" / "acp2_remediation_20261026" / "m4-04-route-a" / "h4.json"
    if not path.is_file():
        return {"receipt": None, "state": "UNKNOWN", "changed_by_this_task": False}
    payload = read_json(path)
    return {
        "receipt": str(path.relative_to(REPO)),
        "state": str(payload.get("state") or "UNKNOWN"),
        "closed": bool(payload.get("closed")),
        "changed_by_this_task": False,
        "note": (
            "M4-06b does not add DA/Recipe evidence; H4 remains fed by M4-04 route-a "
            "and is reported here only as terminal-judgement context."
        ),
    }


def h6_input(ctx: Context, status: str) -> dict[str, Any]:
    m4_06a_summary = REPO / "reports" / "acp2_remediation_20261026" / "m4-06a" / "summary.json"
    a_status = "UNKNOWN"
    if m4_06a_summary.is_file():
        payload = read_json(m4_06a_summary)
        a_status = str(payload.get("status") or "UNKNOWN")
    coverage = read_json(
        REPO
        / "docs"
        / "reference"
        / "next-change"
        / "acp2-contracts"
        / "release"
        / "evidence-coverage.current.json"
    )
    h6 = next(row for row in coverage["h_rows"] if row["id"] == "H6")
    both = status == "PASS" and a_status == "PASS"
    return {
        "schema": "acp2.m406b.h6-input.v1",
        "generated_at": utc_now(),
        "h6_before": h6,
        "halves": {
            "M4-06a": {
                "receipt": "reports/acp2_remediation_20261026/m4-06a/summary.json",
                "receipt_sha256": (
                    file_digest(m4_06a_summary) if m4_06a_summary.is_file() else None
                ),
                "status": a_status,
                "scope": "upgrade/rollback/failure-injection (数据库/schema、Controller、备份恢复)",
            },
            "M4-06b": {
                "receipt": "reports/acp2_remediation_20261026/m4-06b/summary.json",
                "status": status,
                "scope": ("NMR/热化学跨领域复用 + 多项目/多用户/多假设隔离 + 撤权三面一致"),
            },
        },
        "h4": h4_snapshot(),
        "h6_judgement": "CLOSEABLE" if both else "OPEN",
        "coverage_recommendation": {
            "file": (
                "docs/reference/next-change/acp2-contracts/release/evidence-coverage.current.json"
            ),
            "row": "H6",
            "result": "PASS" if both else h6.get("result"),
            "status": "CLOSED" if both else h6.get("status"),
            "science_state": "NOT_VERIFIED" if not both else h6.get("science_state"),
            "reason": (
                "EXECUTION_PLAN M4-06 退出条件：M4-06a 的升级/回滚与 M4-06b 的跨领域、"
                "多项目多用户分别取得当前候选原生回执后，H6 才关闭。"
            ),
            "not_applied_by_this_run": True,
            "science_state_note": (
                "science_state belongs to the H7 handoff prerequisites and is not "
                "changed by this stage verdict; only the H6 stage row is judged."
            ),
            "per_gap_action": (
                "update each gap's result/status from its owning task receipt; this "
                "run only supplies G18's cross-domain/multi-tenant half"
            ),
            "orchestrator_action": (
                "apply only after reviewing both receipts and the per-gap status of H6's "
                "own gap list; this run never edits the coverage file"
            ),
        },
        "residual_risks": [
            "H6's gap list (G09/G12/G13/G14/G18/G20/G23/G24/G25/G26) is broader than this task; "
            "the per-gap result/status must be updated from each owning task's receipt.",
            "the NMR shielding table and experimental spectrum are declared fixture stubs; the "
            "row proves contract/permission reuse, not quantitative NMR accuracy.",
        ],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(REPO / "reports" / "acp2_remediation_20261026" / "m4-06b"),
        help="Evidence output directory",
    )
    parser.add_argument(
        "--run-root",
        default=str(RUN_ROOT),
        help="Isolated run root (default: /ACPServer/acp2-runs/remediation/m4-06b)",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the isolated run root")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    global RUN_ROOT
    RUN_ROOT = Path(args.run_root).resolve()
    production = Path("/ACPServer/acp2-runs/remediation")
    if RUN_ROOT == production or RUN_ROOT == production / "run":
        raise SystemExit("the production run root is forbidden; pass an isolated --run-root")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if RUN_ROOT.exists() and not args.keep:
        shutil.rmtree(RUN_ROOT)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    ctx = Context(
        out=out,
        work=RUN_ROOT / "work",
        run_root=RUN_ROOT / "run-root",
        identity_db=RUN_ROOT / "identity" / "acp_identity.db",
        jobs_db=RUN_ROOT / "run-root" / "acp_jobs.db",
        admission_db=RUN_ROOT / "run-root" / "acp_admission.db",
        resources_db=RUN_ROOT / "run-root" / "acp_research.db",
    )
    for path in (ctx.work, ctx.run_root, ctx.identity_db.parent, ctx.resources_db.parent):
        path.mkdir(parents=True, exist_ok=True)
    status = "FAIL"
    reason = ""
    started_at = utc_now()
    try:
        missing = []
        xtb_bin = shutil.which("xtb") or ""
        if not xtb_bin:
            missing.append("xtb")
        shermo_bin = find_shermo()
        if not shermo_bin:
            missing.append("shermo")
        if not ROUTE_A_FREQ_LOG.is_file():
            missing.append(f"m4-04-route-a-freq-log:{ROUTE_A_FREQ_LOG}")
        if missing:
            status = "NOT_VERIFIED"
            reason = "missing prerequisites: " + ", ".join(missing)
            print(f"NOT_VERIFIED: {reason}", file=sys.stderr)
            write_json(
                out / "summary.json",
                {
                    "schema": SCHEMA,
                    "status": status,
                    "reason": reason,
                    "generated_at": utc_now(),
                    "started_at": started_at,
                },
            )
            return 2
        # The acceptance process runs the same CCCP task cores in-process; point
        # them at the resolved binaries exactly like the Controller child.
        os.environ["CONFSEARCH_SHERMO_PATH"] = shermo_bin
        os.environ["CONFSEARCH_XTB_PATH"] = xtb_bin
        os.environ["ACP_IDENTITY_MODE"] = "api-key"
        os.environ["ACP_IDENTITY_DB"] = str(ctx.identity_db)
        os.environ["ACP_IDENTITY_TENANT"] = TENANT
        ctx.release = local_release()
        provision_identity(ctx)
        seed_projects_and_tasks(ctx)
        budgets = define_budgets(ctx)
        ledger = budgets["ledger"]
        controller_env = {
            "ACP_IDENTITY_MODE": "api-key",
            "ACP_IDENTITY_DB": str(ctx.identity_db),
            "ACP_IDENTITY_TENANT": TENANT,
            "ACP_POLL_INTERVAL": "5",
            "CONFSEARCH_XTB_PATH": xtb_bin,
            "CONFSEARCH_SHERMO_PATH": shermo_bin,
            "ACP2_M406B_OUT": str(out),
        }
        ctx.controller = ControllerProcess(
            workdir=RUN_ROOT / "controller",
            run_root=ctx.run_root,
            log_path=out / "controller.log",
            env=controller_env,
        )
        ctx.controller.start()

        nmr_budget = __import__(
            "acp_services.runtime.research_budget", fromlist=["budget_id_for"]
        ).budget_id_for(PROJECTS[0], HYPOTHESES[0])
        nmr = run_nmr_row(ctx, ledger, budget_id=nmr_budget)
        thermo = run_thermochemistry_row(ctx, ledger, budget_id=nmr_budget)
        reuse = cross_domain_reuse(nmr, thermo)
        archived = copy_cross_domain_evidence(ctx)
        ctx.add(
            check(
                "cross_domain_nmr", nmr["comparison"]["status"] == "supported", nmr["evidence_kind"]
            )
        )
        ctx.add(
            check(
                "cross_domain_thermochemistry",
                thermo["ensemble"]["status"] == "supported",
                thermo["evidence_kind"],
            )
        )
        ctx.add(
            check("cross_domain_reuse", reuse["passed"], "capability/budget/evidence/viewmodel")
        )

        memory = research_memory_matrix(ctx)
        approvals = approval_matrix(ctx)
        budgets_matrix = budget_matrix(ledger, budgets["policies"])
        taskref = taskref_matrix(ctx, nmr)
        artifacts = artifact_matrix(ctx)
        events = event_matrix(ctx)
        scopes = capability_scope_matrix(ctx)
        ctx.add(check("research_memory_isolation", memory["passed"], "6 concurrent proposals"))
        ctx.add(check("approval_isolation", approvals["passed"], "6 approvals"))
        ctx.add(check("budget_isolation", budgets_matrix["passed"], "no cross-project transfer"))
        ctx.add(check("taskref_isolation", taskref["passed"], "same task id, two projects"))
        ctx.add(check("artifact_isolation", artifacts["passed"], "same relative path"))
        ctx.add(check("event_stream_isolation", events["passed"], "single task scope"))
        ctx.add(check("capability_scope", scopes["passed"], "forms + plugin snapshot"))

        revocation = revocation_three_face(ctx)
        ctx.add(check("revocation_three_face", revocation["passed"], "HTTP/MCP/CLI"))

        failed = [row for row in ctx.checks if row.status != "PASS"]
        status = "PASS" if not failed else "FAIL"
        write_json(out / "controller-calls.json", ctx.controller.calls)
        ctx.records["controller-calls.json"] = {
            "path": "controller-calls.json",
            "sha256": file_digest(out / "controller-calls.json"),
        }
        summary = {
            "schema": SCHEMA,
            "status": status,
            "generated_at": utc_now(),
            "started_at": started_at,
            "out": str(out),
            "run_root": str(RUN_ROOT),
            "git": git_facts(),
            "release": ctx.release,
            "controller": {
                "base_url": ctx.controller.base_url,
                "calls": len(ctx.controller.calls),
                "calls_record": "controller-calls.json",
                "log": "controller.log",
                "log_bytes": (
                    (out / "controller.log").stat().st_size
                    if (out / "controller.log").is_file()
                    else 0
                ),
            },
            "rows": {
                "cross_domain_nmr": nmr,
                "cross_domain_thermochemistry": thermo,
            },
            "cross_domain_reuse": reuse,
            "cross_domain_artifacts": archived,
            "multitenant": {
                "research_memory": memory,
                "approvals": approvals,
                "budgets": budgets_matrix,
                "taskref": taskref,
                "artifacts": artifacts,
                "events": events,
                "capability_scope": scopes,
            },
            "revocation": revocation,
            "checks": [row.to_dict() for row in ctx.checks],
            "records": ctx.records,
            "limitations": [
                "NMR shielding table + experimental spectrum are the declared A-24 "
                "fixtures; only the xTB structure, the comparison, the projection and "
                "the frame contract are native.",
                "Thermochemistry reuses the M4-04 route-a frequency log and SP energy; it does not "
                "recompute the endpoint SP/frequency.",
                "No browser row: multi-tenant surfaces are HTTP/MCP/CLI by design.",
                "M4-06b-F1: the served capability-forms document covers the built-in "
                "capability subset; the NMR/thermochemistry domain descriptors are forwarded by "
                "GET /science/capabilities but have no ui-subset form of their own.",
                "M4-06b-F2: the remote CLI task surface previously forwarded no credential; it now "
                "uses ACP_API_KEY exactly like `acp mcp serve`.",
                "H6's gap list is broader than this task; per-gap coverage stays with each owner.",
            ],
        }
        summary["h6_input"] = h6_input(ctx, status)
        ctx.record("summary.json", summary)
        ctx.record("cross-domain-nmr.json", nmr)
        ctx.record("cross-domain-thermochemistry.json", thermo)
        ctx.record("multitenant-matrix.json", summary["multitenant"])
        ctx.record("revocation-three-face.json", revocation)
        ctx.record("h6-input.json", summary["h6_input"])
        write_readme(out, summary)
        return 0 if status == "PASS" else 1
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - acceptance surfaces its own failure
        import traceback

        traceback.print_exc()
        status = "FAIL"
        reason = f"{type(exc).__name__}: {exc}"
        failure = {
            "schema": SCHEMA,
            "status": status,
            "generated_at": utc_now(),
            "started_at": started_at,
            "out": str(out),
            "reason": reason,
            "checks": [row.to_dict() for row in ctx.checks],
            "controller_log_tail": ctx.controller.log_tail() if ctx.controller else "",
        }
        ctx.records["summary.json"] = {
            "path": "summary.json",
            "sha256": None,
        }
        write_json(out / "summary.json", failure)
        return 1
    finally:
        if ctx.controller is not None:
            ctx.controller.stop()
        if not args.keep:
            shutil.rmtree(RUN_ROOT, ignore_errors=True)


def write_readme(out: Path, summary: Mapping[str, Any]) -> None:
    nmr = summary["rows"]["cross_domain_nmr"]
    thermo = summary["rows"]["cross_domain_thermochemistry"]
    reuse = summary["cross_domain_reuse"]
    matrix = summary["multitenant"]
    revocation = summary["revocation"]
    h6 = summary["h6_input"]
    before = revocation["before"]
    after = revocation["after"]
    status_by_check = {row["name"]: row["status"] for row in summary["checks"]}

    def column(name: str) -> str:
        return status_by_check.get(name, "FAIL")

    lines = [
        "# M4-06b — 跨领域 / 多租户 required 行（H6 后半）",
        "",
        f"**Status: {summary['status']}** · schema `{summary['schema']}` · "
        f"HEAD `{summary['git']['head'][:12]}`",
        f"Generated by `scripts/acceptance/acp2_m406b.py`; isolated run root "
        f"`{summary['run_root']}`; production run root never used.",
        "",
        "## 跨领域两行（同一公共 capability / 预算 / EvidenceReport / ViewModel）",
        "",
        "| row | domain | evidence_kind | capability path | report digest | projection |",
        "|---|---|---|---|---|---|",
        f"| NMR | `{nmr['domain']}` | `{nmr['evidence_kind']}` | "
        f"`{nmr['capability']['catalog_sha256'][:22]}…` | "
        f"`{nmr['digest_chain']['report'][:22]}…` | "
        f"`{nmr['digest_chain']['projection'][:22]}…` |",
        f"| 热化学 | `{thermo['domain']}` | `{thermo['evidence_kind']}` | "
        f"`{thermo['capability']['catalog_sha256'][:22]}…` | "
        f"`{thermo['digest_chain']['report'][:22]}…` | "
        f"`{thermo['digest_chain']['projection'][:22]}…` |",
        "",
        "Reuse: "
        f"capability={reuse['capability']['passed']} "
        f"(forms={reuse['capability']['forms']['passed']}), "
        f"budget={reuse['budget']['passed']}, "
        f"evidence={reuse['evidence']['passed']}, "
        f"viewmodel={reuse['viewmodel']['passed']}.",
        "",
        "## 多租户矩阵（3 项目 × 3 用户 × 2 假设）",
        "",
        "| 项目 | 用户 | 假设 | Proposal CAS | Approval | Budget | TaskRef | "
        "Artifact | Event | Plugin snapshot |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for project in PROJECTS:
        lines.append(
            f"| `{project}` | `{USERS[project][0]}` | `{', '.join(HYPOTHESES)}` | "
            f"{column('research_memory_isolation')} | {column('approval_isolation')} | "
            f"{column('budget_isolation')} | {column('taskref_isolation')} | "
            f"{column('artifact_isolation')} | {column('event_stream_isolation')} | "
            f"{column('capability_scope')} |"
        )
    denied = len([row for row in matrix["research_memory"]["cross_project_reads"] if row["denied"]])
    lines.extend(
        [
            "",
            f"- research memory: `{matrix['research_memory']['created']}` proposals, "
            f"cross reads denied `{denied}/6`",
            f"- approvals: `{len(matrix['approvals']['approvals'])}` approved, "
            f"project-scoped query leak-free="
            f"{matrix['approvals']['query_is_project_scoped']}",
            f"- budgets: distinct ids={matrix['budgets']['distinct_budget_ids']}, "
            f"cross-project settlement applied="
            f"{matrix['budgets']['cross_budget_settlement']['applied']}, "
            f"tiny budget second reservation admitted="
            f"{matrix['budgets']['tiny_budget']['second']['admitted']}",
            f"- TaskRef: same task id in two projects="
            f"`{matrix['taskref']['same_task_id_different_project']}`, "
            f"foreign projection refused="
            f"`{matrix['taskref']['foreign_project_refused']}`",
            f"- events: own={matrix['events']['own_events']}, cross denied="
            f"`{matrix['events']['cross_authorized'] is False}`",
            f"- capability-forms: same bytes across projects="
            f"`{matrix['capability_scope']['shared_catalog_same_bytes']}`, "
            f"cross project 404=`{matrix['capability_scope']['scope_enforced']}`",
            f"- cache: shared science catalog warmed by "
            f"`{matrix['capability_scope']['cache']['cached_catalog_warmed_by']}` projects; "
            f"cross reads after warm="
            f"`{matrix['capability_scope']['cache']['cross_read_after_warm_status']}`",
            "",
            "## 撤权三面一致",
            "",
            "| face | before | after |",
            "|---|---|---|",
            f"| HTTP `GET /api/identity` + task read | "
            f"{before['http_identity']} / {before['http_task']} | "
            f"{after['http_identity']} / {after['http_task']} |",
            f"| MCP stdio `acp_status` | "
            f"isError={before['mcp_status_is_error']} | "
            f"isError={after['mcp_is_error']} (`{after['mcp_error_code']}`) |",
            f"| CLI `acp task query --controller` | "
            f"rc={before['cli']['returncode']} | "
            f"rc={after['cli']['returncode']} (`{after['cli']['code']}`) |",
            "",
            "## H6 判定输入",
            "",
            f"- M4-06a: `{h6['halves']['M4-06a']['status']}`",
            f"- M4-06b: `{h6['halves']['M4-06b']['status']}`",
            f"- H6 judgement: **{h6['h6_judgement']}**",
            f"- coverage recommendation (not applied): "
            f"`{h6['coverage_recommendation']['result']}` / "
            f"`{h6['coverage_recommendation']['status']}`",
            "",
            "## 声明边界 / 遗留",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "set -a; . /ACPServer/acp2-runs/remediation/env.sh; set +a",
            "PYTHONPATH=src python3.11 scripts/acceptance/acp2_m406b.py "
            "--out reports/acp2_remediation_20261026/m4-06b/",
            "PYTHONPATH=src python3.11 -m pytest "
            "tests/test_acp2_m406b_cross_domain.py -q --run-slow",
            "```",
            "",
        ]
    )
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
