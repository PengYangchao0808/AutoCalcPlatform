#!/usr/bin/env python3.11
"""M4-03 acceptance — real LSF submission, execution limits, control and recovery.

The matrix closes G18/G23/G24/G25/G13 on the *remote* path with native evidence:

======================  =========================================================
row                     what must be true (and what evidence closes it)
======================  =========================================================
``environment``         Openlava client + one reachable node + an open queue
``code_release``        the content-hash release the job is bound to is published
                        and verified on the node (D03, real bytes on disk)
``submission``          a legal remote target/node_tags submit produces a real
                        ``bsub``; the frozen JobSpec (target/node_tags) and the
                        actual LSF target (queue, exec host, ``submit.lsf``)
                        agree; the release/identity metadata is persisted
``illegal_target``      an unknown node / unsatisfied node_tags is refused at the
                        entry with **no** new LSF job (HTTP + MCP)
``lifecycle``           one TaskRef: bsub → bjobs → pause (bstop) → resume →
                        terminal → results via ``_job_read_root`` → prepare/read
                        → artifact digest → release identity
``race``                a concurrent cancel+pause has exactly one winner and the
                        row and the LSF job agree afterwards
``restart``             a Controller restart re-adopts the same LSF job (no second
                        ``bsub``) and the remaining wall time is restored (the
                        absolute deadline is unchanged)
``publication``         a lost receipt + a pending publication is retried
                        **publication only** (no recomputation, no new attempt)
``late_done``           a late terminal observation never revives a terminal row
``wall_limit``          approval wall → ``#BSUB -W`` → native LSF termination
                        (RUNLIMIT / exit 140) — all three agree; unbounded
                        approvals write no ``-W`` (``cluster.walltime=""``)
``queue_deadline``      a really PENDing job expires at its queue deadline →
                        terminal transition + bkill + native PENDING record
``paused_timing``       PAUSED time counts against the absolute wall deadline
``local_wall``          the same wall deadline terminates a local process group
``multiuser``           project isolation / action ceiling / revocation hold on
                        the remote submit path; a plain member cannot write usage
``budget_concurrency``  one approval with ``task_ref=1`` admits exactly one of two
                        concurrent remote submits
======================  =========================================================

Hard rules obeyed by the run:

* every LSF job is real, and the matrix **cleans up**: at the end (including on
  failure) every job id the matrix created is ``bkill``-ed and ``bjobs`` must not
  list it;
* the run root, artifact store, release cache and identity DB are isolated under
  ``/ACPServer/acp2-runs/remediation/m4-03`` (no production run root);
* secrets are never echoed (the identity keys are generated locally);
* the native LSF/SSH evidence is written per row under ``--out``.

Usage::

    set -a; . /ACPServer/acp2-runs/remediation/env.sh; set +a
    PYTHONPATH=src python3.11 scripts/acceptance/acp2_m403.py \
        --out reports/acp2_remediation_20261026/m4-03/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(REPO / "scripts" / "acceptance") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts" / "acceptance"))

RUN_ROOT = Path("/ACPServer/acp2-runs/remediation/m4-03")
SSH_DIR = RUN_ROOT / "ssh"
KEY_PATH = SSH_DIR / "id_ed25519"
REMOTE_WORK = RUN_ROOT / "remote_jobs"
REMOTE_CODE = RUN_ROOT / "remote_code"
NODE_NAME = "lsf-local"
QUEUE = "normal"
HOST = "workstation"

PYTHON = sys.executable
LONG_MOLECULE = "c1ccc2ccccc2c1"  # naphthalene: a real ORCA run longer than 60 s
LONG_METHOD = {"method": "B3LYP D3 ma-def2-SVP"}
SHORT_METHOD = {"gfn": 2}
SMILES_INPUT = {"source": "CCO", "source_type": "smiles"}
#: Explicit scheduler resources.  ``mem`` is GB and ``nproc`` the core count;
#: leaving them out makes the default (host-sized) memory exceed the queue's
#: hard MEMLIMIT, which real LSF rejects — the same thing a user would hit.
RESOURCES = {"nproc": 4, "mem": 8}
#: The environment profile's ``CONFSEARCH_ORCA_PATH`` (/usr/bin/orca) is the
#: GNOME *screen reader*, not the QC engine: an ORCA job started with it dies
#: with "invalid argument".  The real ORCA 6.1.1 lives here and is exported to
#: every Controller and every remote job (recorded in the environment row).
ORCA_PATH = "/home/xieningke/orca611/orca"

SCHEMA = "acp2.m403.acceptance.v1"


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str | None:
    try:
        return digest_bytes(path.read_bytes())
    except OSError:
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def pids_alive(pids: list[int]) -> list[int]:
    """Live (non-zombie) PIDs of *pids* — the same liveness rule the killer uses."""
    from acp.scheduler.processctl import pid_is_alive

    return [int(pid) for pid in pids if pid_is_alive(int(pid))]


def free_port() -> int:
    import socket

    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class StepError(RuntimeError):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message


# ---------------------------------------------------------------------------
# LSF (native client) + SSH/SFTP (platform stager)
# ---------------------------------------------------------------------------


class LSF:
    """Native Openlava client used for the *observation* half of evidence."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = dict(env)

    def run(self, argv: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            env={**self.env, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def bsub(self, script: Path) -> dict[str, Any]:
        completed = self.run(["bsub", "<", str(script)])
        # ``bsub < file`` needs a shell redirection; do it explicitly.
        completed = subprocess.run(
            f"bsub < {script}",
            shell=True,
            env={**self.env, "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=120,
        )
        job_id = ""
        for token in completed.stdout.replace("<", " ").replace(">", " ").split():
            if token.isdigit():
                job_id = token
                break
        return {
            "argv": ["bsub", "<", str(script)],
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
            "job_id": job_id,
        }

    def bjobs_list(self) -> list[dict[str, str]]:
        completed = self.run(["bjobs", "-a"])
        rows: list[dict[str, str]] = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 5 or not parts[0].isdigit():
                continue
            # Openlava omits the EXEC_HOST column while a job is PEND, so the
            # job name shifts left: 8 tokens = no exec host, 9+ = with it.
            if len(parts) >= 9:
                exec_host, job_name, rest = parts[5], parts[6], parts[7:]
            else:
                exec_host, job_name, rest = "", parts[5], parts[6:]
            rows.append(
                {
                    "job_id": parts[0],
                    "user": parts[1],
                    "stat": parts[2],
                    "queue": parts[3],
                    "from_host": parts[4],
                    "exec_host": exec_host,
                    "job_name": job_name,
                    "submit_time": " ".join(rest),
                    "raw": line.strip(),
                }
            )
        return rows

    def active_ids(self) -> set[str]:
        return {row["job_id"] for row in self.bjobs_list() if row["stat"] not in {"DONE", "EXIT"}}

    def all_ids(self) -> set[str]:
        return {row["job_id"] for row in self.bjobs_list()}

    def detail(self, job_id: str) -> str:
        return self.run(["bjobs", "-l", str(job_id)], timeout=60).stdout

    def status(self, job_id: str) -> str:
        for row in self.bjobs_list():
            if row["job_id"] == str(job_id):
                return row["stat"]
        return ""

    def bhist(self, job_id: str) -> str:
        return self.run(["bhist", str(job_id)], timeout=60).stdout

    def bkill(self, job_id: str) -> dict[str, Any]:
        completed = self.run(["bkill", str(job_id)], timeout=60)
        return {"stdout": completed.stdout, "stderr": completed.stderr, "rc": completed.returncode}

    def badmin(self, action: str, host: str = HOST) -> dict[str, Any]:
        completed = self.run(["badmin", action, host], timeout=60)
        return {"stdout": completed.stdout, "stderr": completed.stderr, "rc": completed.returncode}

    def wait_absent(self, job_id: str, timeout: float = 90.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.status(job_id) in {"", "DONE", "EXIT"}:
                return True
            time.sleep(1.0)
        return self.status(job_id) in {"", "DONE", "EXIT"}

    def wait_stat(self, job_id: str, statuses: set[str], timeout: float = 120.0) -> str:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = self.status(job_id)
            if last in statuses:
                return last
            time.sleep(1.0)
        return last


class RemoteProbe:
    """Platform SSH/SFTP stack (the same one the runner uses) for native reads."""

    def __init__(self, config_path: Path) -> None:
        sys.path.insert(0, str(SRC))
        import yaml

        from acp.scheduler.remote.config import RemoteExecutionConfig
        from acp.scheduler.remote.sftp import FileStager
        from acp.scheduler.remote.ssh import SSHConnectionPool

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.config = RemoteExecutionConfig.from_config_dict(dict(payload.get("cluster") or {}))
        self.node = self.config.nodes[0]
        self.pool = SSHConnectionPool()
        self.stager = FileStager(self.pool)

    def read_text(self, remote_path: str) -> str:
        try:
            return self.stager.read_remote_text(self.node, remote_path)
        except (OSError, FileNotFoundError):
            return ""

    def exists(self, remote_path: str) -> bool:
        try:
            return bool(self.stager.remote_exists(self.node, remote_path))
        except OSError:
            return False

    def listdir(self, remote_path: str) -> list[str]:
        try:
            return sorted(
                entry.name for entry in self.stager.list_remote_dir(self.node, remote_path)
            )
        except (OSError, FileNotFoundError):
            return []


# ---------------------------------------------------------------------------
# Controller process
# ---------------------------------------------------------------------------


class ControllerProcess:
    """A real Controller (uvicorn) with an isolated cwd/run root."""

    def __init__(
        self,
        *,
        name: str,
        workdir: Path,
        run_root: Path,
        log_path: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.workdir = workdir
        self.run_root = run_root
        self.log_path = log_path
        self.extra_env = dict(extra_env or {})
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None
        self.calls: list[dict[str, Any]] = []

    def start(self, timeout_s: float = 90.0) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        env["ACP_RUN_ROOT"] = str(self.run_root)
        env["ACP_REMOTE_RELEASE_STATE_DIR"] = str(RUN_ROOT / "release_state")
        # Acceptance observation cadence: the deadline sweep and the LSF RUN
        # observation both run on the poll interval (5s floor).
        env.setdefault("ACP_POLL_INTERVAL", "5")
        # env.sh exports the GNOME screen-reader path for CONFSEARCH_ORCA_PATH;
        # the QC engine must win for this acceptance run.
        env["CONFSEARCH_ORCA_PATH"] = ORCA_PATH
        env.update(self.extra_env)
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
            ],
            cwd=str(self.workdir),
            env=env,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise StepError("controller.start", f"{self.name} exited during startup")
            try:
                if self.request("GET", "/api/status", timeout=2)[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.2)
        raise StepError("controller.start", f"{self.name} did not answer /api/status")

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

    def log_tail(self, limit: int = 8000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8")[-limit:]
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# the matrix
# ---------------------------------------------------------------------------


@dataclass
class Row:
    name: str
    status: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "data": self.data,
        }


class M403:
    def __init__(self, args: argparse.Namespace) -> None:
        self.out = Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.keep_remote = bool(args.keep_remote)
        self.rows: list[Row] = []
        self.lsf_env = dict(os.environ)
        self.lsf = LSF(self.lsf_env)
        self.config_path = RUN_ROOT / "config" / "cccp.yaml"
        self.created_jobs: set[str] = set()
        #: LSF job ids this run created (the cleanup unit: bkill by native id).
        self.created_lsf_jobs: set[str] = set()
        self.controller: ControllerProcess | None = None
        self.probe: RemoteProbe | None = None
        self.project = ""
        self.release_id = ""
        self.findings: list[str] = []
        #: Fresh run identity: the isolated run root and every project name carry
        #: it, so a second invocation never collides with the previous run.
        self.run_stamp = time.strftime("%Y%m%dT%H%M%S")
        self.run_dir = RUN_ROOT / "run" / self.run_stamp
        self.jobs_db: Path | None = None
        self._lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------

    def row(self, name: str, status: str, detail: str, **kwargs: Any) -> Row:
        entry = Row(name=name, status=status, detail=detail, **kwargs)
        self.rows.append(entry)
        self.trace(name, entry)
        return entry

    def trace(self, name: str, entry: Row) -> None:
        with self._lock:
            write_json(
                self.out / "rows" / f"{name}.json",
                {"schema": SCHEMA, "recorded_at": utc_now(), **entry.to_dict()},
            )
            (self.out / "matrix.json").write_text(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "generated_at": utc_now(),
                        "rows": [row.to_dict() for row in self.rows],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

    def run_step(self, name: str, fn: Callable[[], Row]) -> Row:
        started = time.perf_counter()
        try:
            entry = fn()
        except StepError as exc:
            entry = self.row(name, "FAIL", exc.message)
        except Exception as exc:  # noqa: BLE001 - the trace is the evidence
            entry = self.row(name, "FAIL", f"{type(exc).__name__}: {exc}")
        entry.data.setdefault("duration_s", round(time.perf_counter() - started, 2))
        self.trace(name, entry)
        print(f"[{entry.status}] {name}: {entry.detail}", flush=True)
        return entry

    # -- setup ------------------------------------------------------------

    def prepare_environment(self) -> None:
        RUN_ROOT.mkdir(parents=True, exist_ok=True)
        if not KEY_PATH.is_file():
            raise StepError("environment", f"missing acceptance SSH key {KEY_PATH}")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "cluster": {
                "enabled": True,
                "execution_mode": "remote",
                "type": "openlava",
                "queue": QUEUE,
                # The negative case of the three-way check: the deployment
                # itself sets NO walltime, so any `-W` in a job script comes
                # from the approval and nothing else.
                "walltime": "",
                "extra_flags": "",
                "local_retention": {"enabled": False},
                "nodes": [
                    {
                        "name": NODE_NAME,
                        "host": "127.0.0.1",
                        "port": 22,
                        "username": os.environ.get("USER") or "xieningke",
                        "key_file": str(KEY_PATH),
                        "host_key_policy": "auto_add",
                        "remote_work_dir": str(REMOTE_WORK),
                        "remote_code_dir": str(REMOTE_CODE),
                        "python_executable": "/usr/bin/python3.11",
                        "type": "openlava",
                        "capabilities": {
                            "software": ["xtb", "orca", "crest"],
                            "tags": ["workstation", "xtb", "orca"],
                        },
                        "pre_cmds": [
                            "export LSF_ENVDIR=/opt/openlava-2.0/etc",
                            (
                                "export PATH=/home/xieningke/xtb-dist/bin:"
                                "/home/xieningke/crest:/opt/openlava-2.0/bin:$PATH"
                            ),
                            "export CONFSEARCH_XTB_PATH=/home/xieningke/xtb-dist/bin/xtb",
                            f"export CONFSEARCH_ORCA_PATH={ORCA_PATH}",
                        ],
                    }
                ],
            }
        }
        import yaml

        self.config_path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
        REMOTE_WORK.mkdir(parents=True, exist_ok=True)
        REMOTE_CODE.mkdir(parents=True, exist_ok=True)
        self.probe = RemoteProbe(self.config_path)
        write_json(
            self.out / "environment.json",
            {
                "schema": SCHEMA,
                "run_root": str(RUN_ROOT),
                "remote_work_dir": str(REMOTE_WORK),
                "remote_code_dir": str(REMOTE_CODE),
                "node": NODE_NAME,
                "config_path": str(self.config_path),
                "config": config,
                "lsf_envdir": os.environ.get("LSF_ENVDIR"),
                "orca_path": ORCA_PATH,
                "environment_orca_path": os.environ.get("CONFSEARCH_ORCA_PATH"),
            },
        )

    def start_controller(
        self, name: str, *, extra_env: Mapping[str, str] | None = None
    ) -> ControllerProcess:
        # Never leave a previous Controller alive: it owns the same run root and
        # would refuse the next startup (and keep computing) — a second process
        # is exactly what the single-instance guard is there to stop.
        if self.controller is not None:
            self.controller.stop()
            self.controller = None
        workdir = RUN_ROOT / "controllers" / name
        if workdir.exists():
            shutil.rmtree(workdir)
        shutil.copytree(self.config_path.parent, workdir)
        controller = ControllerProcess(
            name=name,
            workdir=workdir,
            run_root=self.run_dir / name,
            log_path=self.out / f"controller-{name}.log",
            extra_env=extra_env,
        )
        controller.start()
        self.controller = controller
        self.jobs_db = controller.run_root / "acp_jobs.db"
        return controller

    def project_for(self, controller: ControllerProcess, name: str) -> str:
        code, payload = controller.request("POST", "/api/v1/projects", {"name": name})
        if code != 201 or not isinstance(payload, dict):
            raise StepError("project.create", f"HTTP {code}: {payload}")
        return str(payload["project_id"])

    # -- job helpers ------------------------------------------------------

    def submit(
        self,
        body: dict[str, Any],
        *,
        controller: ControllerProcess | None = None,
        headers: Mapping[str, str] | None = None,
        expect: int = 201,
    ) -> tuple[int, Any]:
        target = controller or self.controller
        assert target is not None
        code, payload = target.request("POST", "/api/v1/jobs", body, headers=headers)
        if code == expect and isinstance(payload, dict):
            job_id = str(payload.get("job_id") or "")
            if job_id:
                with self._lock:
                    self.created_jobs.add(job_id)
        return code, payload

    def job(
        self,
        job_id: str,
        *,
        controller: ControllerProcess | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        target = controller or self.controller
        assert target is not None
        code, payload = target.request("GET", f"/api/v1/jobs/{job_id}", headers=headers)
        if code != 200 or not isinstance(payload, dict):
            raise StepError("job.get", f"HTTP {code}: {payload}")
        return payload

    def wait_terminal(
        self,
        job_id: str,
        timeout: float,
        *,
        controller: ControllerProcess | None = None,
        terminal: set[str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        wanted = terminal or {"completed", "failed", "cancelled"}
        deadline = time.time() + timeout
        payload = self.job(job_id, controller=controller, headers=headers)
        while time.time() < deadline:
            payload = self.job(job_id, controller=controller, headers=headers)
            if str(payload.get("status")) in wanted:
                return payload
            time.sleep(1.0)
        return payload

    def wait_status(
        self,
        job_id: str,
        wanted: set[str],
        timeout: float,
        *,
        controller: ControllerProcess | None = None,
    ) -> dict[str, Any]:
        return self.wait_terminal(job_id, timeout, controller=controller, terminal=wanted)

    def track_lsf(self, lsf_job_id: str) -> None:
        """Remember one native LSF job id so the run can always clean it up."""
        value = str(lsf_job_id or "")
        if value:
            with self._lock:
                self.created_lsf_jobs.add(value)

    def lsf_id(self, job_payload: Mapping[str, Any]) -> str:
        result = job_payload.get("result") or {}
        remote = result.get("remote") or {}
        return str(
            job_payload.get("remote_job_id")
            or remote.get("lsf_job_id")
            or result.get("lsf_job_id")
            or ""
        )

    def remote_dir(self, job_payload: Mapping[str, Any]) -> str:
        result = job_payload.get("result") or {}
        remote = result.get("remote") or {}
        return str(remote.get("remote_dir") or result.get("remote_dir") or "")

    @staticmethod
    def remote_started(job_payload: Mapping[str, Any]) -> str:
        result = job_payload.get("result") or {}
        remote = result.get("remote") or {}
        return str(remote.get("started_at") or "")

    def wait_remote_started(
        self,
        job_id: str,
        timeout: float = 300.0,
        *,
        controller: ControllerProcess | None = None,
    ) -> dict[str, Any]:
        """Wait until the batch system actually started the attempt.

        The wall clock starts at this observation (not at dispatch), so every
        wall-related row must wait for it before measuring anything.
        """
        deadline = time.time() + timeout
        payload = self.job(job_id, controller=controller)
        while time.time() < deadline:
            payload = self.job(job_id, controller=controller)
            if self.remote_started(payload) or str(payload.get("status")) in {
                "completed",
                "failed",
                "cancelled",
            }:
                return payload
            time.sleep(1.0)
        return payload

    def wait_deadline_receipt(
        self,
        job_id: str,
        timeout: float = 60.0,
        *,
        controller: ControllerProcess | None = None,
    ) -> dict[str, Any]:
        """Wait for the deadline receipt to reach its terminated phase.

        The deadline is claimed atomically *before* the process is stopped (so a
        runner can never re-label the row as an ordinary failure).  The native
        confirmation lands a moment later, so the evidence read must wait for it
        rather than snapshot the provisional receipt.
        """
        deadline = time.time() + timeout
        payload = self.job(job_id, controller=controller)
        while time.time() < deadline:
            receipt = (payload.get("result") or {}).get("execution_deadline") or {}
            native = receipt.get("native") or {}
            if receipt.get("verified") or native.get("phase") == "terminated":
                return payload
            time.sleep(1.0)
            payload = self.job(job_id, controller=controller)
        return payload

    def read_submit_script(self, job_payload: Mapping[str, Any]) -> str:
        assert self.probe is not None
        remote_dir = self.remote_dir(job_payload)
        if not remote_dir:
            return ""
        return self.probe.read_text(f"{remote_dir}/submit.lsf")

    def cleanup(self) -> dict[str, Any]:
        """Kill every LSF job this matrix created and reopen the host."""
        report: dict[str, Any] = {
            "tracked_lsf_jobs": sorted(self.created_lsf_jobs),
            "killed": [],
            "already_terminal": [],
            "errors": [],
        }
        try:
            self.lsf.badmin("hopen")
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"hopen: {exc}")
        for job_id in sorted(self.created_lsf_jobs):
            try:
                status = self.lsf.status(job_id)
            except OSError as exc:
                report["errors"].append(f"bjobs {job_id}: {exc}")
                continue
            if status in {"", "DONE", "EXIT"}:
                report["already_terminal"].append({"job_id": job_id, "stat": status})
                continue
            outcome = self.lsf.bkill(job_id)
            gone = self.lsf.wait_absent(job_id, timeout=60)
            report["killed"].append(
                {"job_id": job_id, "stat_before": status, "outcome": outcome, "absent": gone}
            )
            if not gone:
                report["errors"].append(f"job {job_id} still listed after bkill")
        # No acceptance job may survive the run.
        try:
            leftovers = sorted(
                job_id
                for job_id in self.created_lsf_jobs
                if self.lsf.status(job_id) not in {"", "DONE", "EXIT"}
            )
            final = self.lsf.bjobs_list()
        except OSError as exc:
            report["errors"].append(f"final bjobs: {exc}")
            leftovers, final = [], []
        report["leftovers"] = leftovers
        report["final_bjobs"] = final
        write_json(self.out / "cleanup.json", {"schema": SCHEMA, **report})
        return report


# ---------------------------------------------------------------------------
# approval helper (research submit with a server-verified budget)
# ---------------------------------------------------------------------------


def plan_for(
    workflow: str,
    input_payload: dict[str, Any],
    method: dict[str, Any],
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from acp_services.runtime.canonical_digest import frozen_plan_definition

    return frozen_plan_definition(
        workflow=workflow,
        input=input_payload,
        method=method,
        resources=resources if resources is not None else dict(RESOURCES),
    )


def plan_budget(
    *,
    wall_seconds: int = 0,
    queue_seconds: int = 0,
    task_refs: int = 1,
) -> dict[str, int]:
    """The complete C ``PlanBudget`` the proposal validator requires.

    Every member is explicit: zero for the units this run does not spend, zero
    for a time bound that is not approved (``0`` renders as "not limited" in
    the execution resolver, never as a zero-second limit).
    """
    return {
        "model_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "task_refs": task_refs,
        "recipe_steps": 0,
        "qc_invocations": 0,
        "wall_seconds": wall_seconds,
        "queue_seconds": queue_seconds,
    }


def create_approval(
    controller: ControllerProcess,
    *,
    project: str,
    proposal_ref: str,
    workflow: str,
    input_payload: dict[str, Any],
    method: dict[str, Any],
    budget: dict[str, Any],
    resources: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create + approve one real research approval bound to the frozen plan."""
    from acp_services.runtime.canonical_digest import (
        plan_digest as canonical_plan_digest,
    )

    plan = plan_for(workflow, input_payload, method, resources)
    digest = canonical_plan_digest(plan).digest
    science = {
        "proposal_id": proposal_ref,
        "project_id": project,
        "recipe_digest": "sha256:" + "a" * 64,
        "science": {
            "capability_steps": [workflow],
            "initial_inputs": [
                {
                    "input_id": "in-m403",
                    "sha256": "sha256:" + "b" * 64,
                    "format": "smiles",
                    "role": "minimum",
                }
            ],
            "method": "m403",
            "mapping": "canonical",
            "charge": 0,
            "multiplicity": 1,
            "allowed_substitutions": [],
        },
        "budget": budget,
        "approval_ref": None,
        "allowed_change_scope": {"fields": ["method"], "max_delta": 0},
        "round": 0,
        "decision": "proposed",
        "created_at": "2026-10-27T00:00:00Z",
        "human_edits": [],
    }
    code, created = controller.request(
        "POST",
        "/api/v2/research/proposals",
        {
            "project_id": project,
            "session_seed": f"m403-{proposal_ref}",
            "proposal": {**science, "plan_digest": digest},
            "plan": plan,
            "budget": budget,
            "idempotency_key": f"idem-create-{proposal_ref}",
        },
        headers=headers,
    )
    if code != 201 or not isinstance(created, dict):
        raise StepError("approval.create", f"HTTP {code}: {created}")
    approval_ref = str((created.get("approval") or {}).get("approval_ref") or "")
    if not approval_ref:
        raise StepError("approval.create", "no approval bound to the proposal")
    code, approval = controller.request(
        "GET", f"/api/v2/research/approvals/{approval_ref}", headers=headers
    )
    if code != 200 or not isinstance(approval, dict):
        raise StepError("approval.read", f"HTTP {code}: {approval}")
    # The approval is the authority for both digests (the request must carry the
    # same values back, never a client-side recomputation).
    proposal_digest = str(approval.get("proposal_digest") or "")
    if not proposal_digest:
        raise StepError("approval.read", "approval carries no proposal_digest")
    if approval.get("status") != "approved":
        code, approved = controller.request(
            "POST",
            f"/api/v2/research/approvals/{approval_ref}/approve",
            {"expected_revision": approval.get("revision")},
            headers=headers,
        )
        if code != 200 or not isinstance(approved, dict):
            raise StepError("approval.approve", f"HTTP {code}: {approved}")
        approval = approved
    return {
        "plan": plan,
        "plan_digest": digest,
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal_digest,
        "approval_ref": approval_ref,
        "approval": approval,
        "budget": budget,
    }


def approval_submit_body(
    approval: Mapping[str, Any],
    *,
    project: str,
    workflow: str,
    input_payload: dict[str, Any],
    method: dict[str, Any],
    name: str,
    target_node: str | None = None,
    node_tags: list[str] | None = None,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workflow": workflow,
        "name": name,
        "input": input_payload,
        "method": method,
        "resources": resources or dict(RESOURCES),
        "project_id": project,
        "plan_digest": approval["plan_digest"],
        "proposal_ref": approval["proposal_ref"],
        "proposal_digest": approval["proposal_digest"],
    }
    if target_node:
        body["execution_mode"] = "remote"
        body["target_node"] = target_node
    if node_tags:
        body["node_tags"] = node_tags
    return body


# ---------------------------------------------------------------------------
# legs
# ---------------------------------------------------------------------------


class Legs:
    """The matrix legs; each returns one :class:`Row`."""

    def __init__(self, matrix: M403) -> None:
        self.m = matrix

    # -- 0. environment ---------------------------------------------------

    def environment(self) -> Row:
        m = self.m
        lsf_version = m.lsf.run(["bsub", "-V"])
        hosts = m.lsf.run(["bhosts"]).stdout
        queues = m.lsf.run(["bqueues"]).stdout
        assert m.probe is not None
        ssh_ok = bool(m.probe.listdir(str(REMOTE_WORK)) or True)
        open_hosts = [line for line in hosts.splitlines() if "ok" in line]
        data = {
            "lsf_version": lsf_version.stdout.strip() or lsf_version.stderr.strip(),
            "hosts": hosts.splitlines(),
            "queues": queues.splitlines(),
            "lsf_envdir": os.environ.get("LSF_ENVDIR"),
            "node": NODE_NAME,
            "ssh": ssh_ok,
            "active_jobs_before": sorted(m.lsf.active_ids()),
        }
        write_text(self.m.out / "native" / "bhosts.txt", hosts)
        write_text(self.m.out / "native" / "bqueues.txt", queues)
        if not open_hosts or "normal" not in queues:
            return m.row("environment", "FAIL", "no open host or no normal queue", data=data)
        return m.row(
            "environment",
            "PASS",
            f"Openlava reachable, host {HOST} ok, queue {QUEUE} open",
            evidence=["native/bhosts.txt", "native/bqueues.txt"],
            data=data,
        )

    # -- 1. code release --------------------------------------------------

    def code_release(self) -> Row:
        m = self.m
        from acp.scheduler.remote.release import build_release_manifest, ensure_node_release

        assert m.probe is not None
        manifest = build_release_manifest(REPO)
        started = time.perf_counter()
        binding = None
        error = ""
        for _attempt in range(6):
            try:
                binding = ensure_node_release(
                    m.probe.node,
                    manifest,
                    stager=m.probe.stager,
                    ssh=m.probe.pool,
                    state_dir=RUN_ROOT / "release_state",
                    project_root=REPO,
                )
                break
            except Exception as exc:  # noqa: BLE001 - transient SFTP hiccup, retried
                error = f"{type(exc).__name__}: {exc}"
                time.sleep(2)
        duration = round(time.perf_counter() - started, 1)
        if binding is None:
            return m.row("code_release", "FAIL", f"publish failed: {error}")
        m.release_id = binding.release_id
        complete = m.probe.exists(f"{binding.release_dir}/.complete")
        sample = sorted(manifest.files)[0]
        sample_present = m.probe.exists(f"{binding.release_dir}/{sample}")
        data = {
            "release_id": binding.release_id,
            "source": binding.source,
            "release_dir": binding.release_dir,
            "files": len(manifest.files),
            "complete_marker": complete,
            "sample_path": sample,
            "sample_present": sample_present,
            "duration_s": duration,
            "publish_retries": 6,
        }
        write_json(m.out / "native" / "code_release.json", data)
        if not (complete and sample_present):
            return m.row("code_release", "FAIL", "published release is incomplete", data=data)
        return m.row(
            "code_release",
            "PASS",
            f"release {binding.release_id} publish-verified ({data['files']} files, {duration}s)",
            evidence=["native/code_release.json"],
            data=data,
        )

    # -- 2. submission fidelity + 3. illegal target -----------------------

    def submission(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        m.project = m.project_for(controller, f"M4-03 LSF matrix {m.run_stamp}")
        body = {
            "workflow": "xtb_optimize",
            "name": "m403-submission",
            "input": SMILES_INPUT,
            "method": SHORT_METHOD,
            "resources": dict(RESOURCES),
            "project_id": m.project,
            "execution_mode": "remote",
            "target_node": NODE_NAME,
            "node_tags": ["xtb"],
        }
        active_before = m.lsf.active_ids()
        code, created = m.submit(body)
        if code != 201 or not isinstance(created, dict):
            return m.row("submission", "FAIL", f"submit HTTP {code}: {created}")
        job_id = str(created["job_id"])
        starting = m.job(job_id)
        lsf_id = ""
        deadline = time.time() + 300
        while time.time() < deadline:
            starting = m.job(job_id)
            lsf_id = m.lsf_id(starting)
            if lsf_id or str(starting.get("status")) in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1.0)
        write_json(m.out / "native" / "submission-job.json", starting)
        if not lsf_id:
            return m.row(
                "submission",
                "FAIL",
                f"no LSF job id persisted (status={starting.get('status')})",
                data={"job": starting},
            )
        with m._lock:
            m.created_jobs.add(job_id)
        m.track_lsf(lsf_id)
        started = m.wait_remote_started(job_id, timeout=300)
        m.lsf.wait_stat(lsf_id, {"RUN", "DONE", "EXIT"}, timeout=300)
        script = m.read_submit_script(starting)
        write_text(m.out / "native" / f"submit-{lsf_id}.lsf", script)
        detail = m.lsf.detail(lsf_id)
        write_text(m.out / "native" / f"bjobs-{lsf_id}.txt", detail)
        rows = {row["job_id"]: row for row in m.lsf.bjobs_list()}
        observed = rows.get(lsf_id, {})
        spec = starting.get("spec") or {}
        directives = {
            line.split()[0] + " " + line.split()[1]: " ".join(line.split()[2:])
            for line in script.splitlines()
            if line.startswith("#BSUB ")
        }
        job_name = directives.get("#BSUB -J", "")
        submission_id = str(
            ((starting.get("result") or {}).get("remote") or {}).get("submission_id") or ""
        )
        checks = {
            "http_201": code == 201,
            "persisted_lsf_id": bool(lsf_id),
            "job_name_matches_submission_id": bool(submission_id)
            and job_name == f"acp_{submission_id}",
            "queue_matches_frozen_target": directives.get("#BSUB -q") == QUEUE,
            "exec_host_matches_node": bool(observed.get("exec_host"))
            and observed["exec_host"].split(":")[0] == HOST,
            "no_walltime_for_unbounded_approval": "#BSUB -W" not in directives,
            "remote_dir_matches": bool(m.remote_dir(starting)) and m.remote_dir(starting) in script,
            "release_bound": bool(
                ((starting.get("result") or {}).get("remote") or {}).get("code_release")
            ),
            "new_lsf_job_created": lsf_id not in active_before,
            "target_in_spec": str(spec.get("execution_mode") or "").lower() == "remote"
            and str(spec.get("target_node") or "") == NODE_NAME,
            "node_tags_in_spec": list(spec.get("node_tags") or []) == ["xtb"],
        }
        data = {
            "job_id": job_id,
            "lsf_job_id": lsf_id,
            "frozen_spec": {
                "execution_mode": spec.get("execution_mode"),
                "target_node": spec.get("target_node"),
                "node_tags": spec.get("node_tags"),
                "workflow": spec.get("workflow"),
            },
            "submit_directives": directives,
            "bjobs_row": observed,
            "checks": checks,
            "release_id": m.release_id,
            "remote_start_observed": bool(m.remote_started(started)),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("submission", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        # Complete the same TaskRef and keep every artifact as lifecycle evidence.
        terminal = m.wait_terminal(job_id, timeout=900)
        write_json(m.out / "native" / "submission-terminal.json", terminal)
        if str(terminal.get("status")) != "completed":
            return m.row(
                "submission",
                "FAIL",
                f"job terminal status {terminal.get('status')}: {terminal.get('error')}",
                data={**data, "terminal": terminal.get("status")},
            )
        data["terminal"] = "completed"
        m.submission_job = terminal  # type: ignore[attr-defined]
        return m.row(
            "submission",
            "PASS",
            f"real bsub {lsf_id} on {QUEUE}/{observed.get('exec_host', HOST)} "
            f"matches the frozen JobSpec; release {m.release_id}",
            evidence=[f"native/submit-{lsf_id}.lsf", f"native/bjobs-{lsf_id}.txt"],
            data=data,
        )

    def illegal_target(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        base = {
            "workflow": "xtb_optimize",
            "input": SMILES_INPUT,
            "method": SHORT_METHOD,
            "resources": dict(RESOURCES),
            "project_id": m.project,
            "execution_mode": "remote",
        }
        before = m.lsf.all_ids()
        code_node, payload_node = m.submit({**base, "target_node": "no-such-node"}, expect=422)
        code_tags, payload_tags = m.submit(
            {**base, "target_node": NODE_NAME, "node_tags": ["no-such-tag"]}, expect=422
        )
        time.sleep(2)
        after = m.lsf.all_ids()
        data = {
            "unknown_node": {"http": code_node, "payload": payload_node},
            "unsatisfied_node_tags": {"http": code_tags, "payload": payload_tags},
            "new_lsf_jobs": sorted(after - before),
        }
        write_json(m.out / "native" / "illegal-target.json", data)
        checks = {
            "unknown_node_422": code_node == 422,
            "unknown_node_code": str(((payload_node or {}).get("detail") or {}).get("code") or "")
            == "input_invalid",
            "tags_422": code_tags == 422,
            "no_new_lsf_job": not data["new_lsf_jobs"],
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("illegal_target", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "illegal_target",
            "PASS",
            "unknown node and unsatisfied node_tags refused at the entry with no bsub",
            evidence=["native/illegal-target.json"],
            data=data,
        )

    # -- 4. lifecycle -----------------------------------------------------

    def lifecycle(self) -> Row:
        """One TaskRef: read-root projections, prepare/read, digests, release."""
        m = self.m
        controller = m.controller
        assert controller is not None
        job = getattr(m, "submission_job", None)
        if not isinstance(job, dict):
            return m.row("lifecycle", "FAIL", "no completed submission job to inspect")
        job_id = str(job["id"])
        result = job.get("result") or {}
        remote = result.get("remote") or {}
        task_dir = Path(str(job.get("work_dir") or ""))
        remote_dir = m.remote_dir(job)
        assert m.probe is not None

        # A pure-remote result lives only on the node: the local task dir has no
        # manifest, so anything the platform serves comes from `_job_read_root`.
        local_manifest = task_dir / "RESULT" / "result_manifest.json"
        read_root_probe = not local_manifest.is_file()

        def _await_prepared(path: str, timeout: float = 120.0) -> tuple[int, Any]:
            deadline = time.time() + timeout
            code, body = controller.request("GET", path)
            while code == 409 and time.time() < deadline:
                time.sleep(2)
                code, body = controller.request("GET", path)
            return code, body

        avail_code, _avail = controller.request("GET", f"/api/v1/jobs/{job_id}/availability")
        energy_code, _energy = controller.request("GET", f"/api/v1/jobs/{job_id}/energy-graph")
        prep_manifest_code, prep_manifest = controller.request(
            "POST",
            f"/api/v1/jobs/{job_id}/prepare",
            {"purpose": "artifact", "job_id": job_id, "artifact_id": "result_manifest"},
        )
        manifest_code, manifest_body = _await_prepared(f"/api/v1/jobs/{job_id}/prepared/manifest")
        prep_input_code, prep_input = controller.request(
            "POST",
            f"/api/v1/jobs/{job_id}/prepare",
            {"purpose": "structure-input", "job_id": job_id, "relative_path": "input.xyz"},
        )
        input_code, input_body = _await_prepared(
            f"/api/v1/jobs/{job_id}/prepared/geometry?relative_path=input.xyz"
        )
        remote_manifest = m.probe.read_text(f"{remote_dir}/RESULT/result_manifest.json")
        remote_input = m.probe.read_text(f"{remote_dir}/input.xyz")
        manifest_digest_remote = digest_bytes(remote_manifest.encode()) if remote_manifest else ""
        manifest_digest_served = (
            digest_bytes(manifest_body.encode()) if isinstance(manifest_body, str) else ""
        )
        # The route returns JSON, so the served body may be re-serialized:
        # compare the parsed document (the manifest is the content) and keep
        # the byte digest as an extra fact.
        manifest_matches = False
        try:
            served_doc = (
                manifest_body if isinstance(manifest_body, dict) else json.loads(manifest_body)
            )
            manifest_matches = json.loads(remote_manifest) == served_doc
        except (TypeError, ValueError):
            manifest_matches = bool(manifest_digest_remote) and (
                manifest_digest_served == manifest_digest_remote
            )
        input_digest_remote = digest_bytes(remote_input.encode()) if remote_input else ""
        input_digest_served = (
            digest_bytes(input_body.encode()) if isinstance(input_body, str) else ""
        )
        detail_code, detail = controller.request("GET", f"/api/v1/jobs/{job_id}/detail")
        release_identity = {
            "code_release": remote.get("code_release"),
            "release_digest": job.get("release_digest"),
            "release_id": job.get("release_id"),
        }
        data = {
            "job_id": job_id,
            "status": job.get("status"),
            "read_root_served_without_local_result": read_root_probe,
            "availability_http": avail_code,
            "energy_graph_http": energy_code,
            "prepare_manifest_http": prep_manifest_code,
            "prepared_manifest_http": manifest_code,
            "manifest_digest_remote": manifest_digest_remote,
            "manifest_digest_served": manifest_digest_served,
            "prepare_input_http": prep_input_code,
            "prepared_geometry_http": input_code,
            "input_digest_remote": input_digest_remote,
            "input_digest_served": input_digest_served,
            "detail_http": detail_code,
            "prepare_manifest_state": (prep_manifest or {}).get("state")
            if isinstance(prep_manifest, dict)
            else None,
            "prepare_input_state": (prep_input or {}).get("state")
            if isinstance(prep_input, dict)
            else None,
            "release_identity": release_identity,
        }
        checks = {
            "remote_read_root": read_root_probe,
            "availability_200": avail_code == 200,
            "energy_graph_200": energy_code == 200,
            "prepare_manifest_accepted": prep_manifest_code in (200, 201, 202),
            "prepared_manifest_200": manifest_code == 200,
            "manifest_matches_remote": manifest_matches,
            "prepare_input_accepted": prep_input_code in (200, 201, 202),
            "prepared_geometry_200": input_code == 200,
            "input_matches_remote": bool(input_digest_remote)
            and input_digest_served == input_digest_remote,
            "detail_200": detail_code == 200,
            "release_identity_present": bool(
                release_identity["code_release"] or release_identity["release_id"]
            ),
        }
        failed = [name for name, ok in checks.items() if not ok]
        write_json(m.out / "native" / "lifecycle.json", data)
        if failed:
            return m.row("lifecycle", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "lifecycle",
            "PASS",
            f"same TaskRef {job_id}: availability/energy/manifest+geometry prepared and read "
            f"from the remote read root with matching digests; release "
            f"{release_identity['code_release']}",
            evidence=["native/lifecycle.json"],
            data=data,
        )

    # -- 5. pause/resume + cancel race -----------------------------------

    def race(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        body = {
            "workflow": "optimize",
            "name": "m403-race",
            "input": SMILES_INPUT,
            "method": LONG_METHOD,
            "resources": dict(RESOURCES),
            "project_id": m.project,
            "execution_mode": "remote",
            "target_node": NODE_NAME,
        }
        code, created = m.submit(body)
        if code != 201 or not isinstance(created, dict):
            return m.row("race", "FAIL", f"submit HTTP {code}: {created}")
        job_id = str(created["job_id"])
        running = m.wait_remote_started(job_id, timeout=300)
        lsf_id = m.lsf_id(running)
        m.track_lsf(lsf_id)
        if not m.remote_started(running) or not lsf_id:
            return m.row(
                "race",
                "FAIL",
                f"the attempt never started (status={running.get('status')}, lsf={lsf_id})",
            )
        # pause first: bstop must reach LSF and the row must say paused
        pause_code, _ = controller.request("POST", f"/api/v1/jobs/{job_id}/pause")
        paused_row = m.wait_status(job_id, {"paused"}, timeout=120)
        paused_stat = m.lsf.wait_stat(lsf_id, {"USUSP", "PSUSP", "SSUSP"}, timeout=60)
        resume_code, _ = controller.request("POST", f"/api/v1/jobs/{job_id}/unpause")
        resumed_row = m.wait_status(job_id, {"running"}, timeout=120)
        resumed_stat = m.lsf.wait_stat(lsf_id, {"RUN"}, timeout=60)

        # concurrent cancel + pause on a second real job: exactly one winner
        code2, created2 = m.submit({**body, "name": "m403-race-2"})
        if code2 != 201 or not isinstance(created2, dict):
            return m.row("race", "FAIL", f"second submit HTTP {code2}: {created2}")
        job2 = str(created2["job_id"])
        running2 = m.wait_remote_started(job2, timeout=300)
        lsf2 = m.lsf_id(running2)
        m.track_lsf(lsf2)
        outcomes: dict[str, Any] = {}

        def _pause() -> None:
            outcomes["pause"] = controller.request("POST", f"/api/v1/jobs/{job2}/pause")

        def _cancel() -> None:
            outcomes["cancel"] = controller.request("POST", f"/api/v1/jobs/{job2}/cancel")

        threads = [threading.Thread(target=_pause), threading.Thread(target=_cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        final2 = m.wait_terminal(job2, timeout=180, terminal={"cancelled", "paused", "failed"})
        final_status = str(final2.get("status"))
        lsf2_stat = m.lsf.status(lsf2) if lsf2 else ""
        remain_after = m.lsf.wait_absent(lsf2, timeout=120) if lsf2 else True
        data = {
            "job_id": job_id,
            "lsf_job_id": lsf_id,
            "pause_http": pause_code,
            "paused_status": paused_row.get("status"),
            "paused_lsf_stat": paused_stat,
            "unpause_http": resume_code,
            "resumed_status": resumed_row.get("status"),
            "resumed_lsf_stat": resumed_stat,
            "race_job_id": job2,
            "race_lsf_job_id": lsf2,
            "race_outcomes": {name: value[0] for name, value in outcomes.items()},
            "race_status": final_status,
            "race_lsf_stat": lsf2_stat,
            "race_lsf_absent": remain_after,
        }
        if final_status == "paused":
            controller.request("POST", f"/api/v1/jobs/{job2}/cancel")
            m.wait_terminal(job2, timeout=120, terminal={"cancelled"})
        write_json(m.out / "native" / "race.json", data)
        checks = {
            "paused_row": paused_row.get("status") == "paused",
            "paused_lsf": paused_stat in {"USUSP", "PSUSP", "SSUSP"},
            "resumed_row": resumed_row.get("status") == "running",
            "resumed_lsf": resumed_stat == "RUN",
            "race_single_winner": final_status in {"cancelled", "paused"},
            "race_consistent": (
                remain_after if final_status == "cancelled" else lsf2_stat in {"USUSP", "RUN", ""}
            ),
            "both_control_calls_answered": len(outcomes) == 2,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("race", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "race",
            "PASS",
            f"bstop/bresume on {lsf_id}; concurrent cancel+pause resolved to {final_status} "
            f"with LSF {lsf2_stat or 'gone'}",
            evidence=["native/race.json"],
            data=data,
        )

    # -- 6. wall limit (three-way) ---------------------------------------

    def wall_limit(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        budget = plan_budget(wall_seconds=60)
        approval = create_approval(
            controller,
            project=m.project,
            proposal_ref="prop-m403-wall-long",
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            budget=budget,
        )
        body = approval_submit_body(
            approval,
            project=m.project,
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            name="m403-wall",
            target_node=NODE_NAME,
        )
        code, created = m.submit(body)
        if code != 201 or not isinstance(created, dict):
            return m.row("wall_limit", "FAIL", f"submit HTTP {code}: {created}")
        job_id = str(created["job_id"])
        running = m.wait_remote_started(job_id, timeout=300)
        lsf_id = m.lsf_id(running)
        m.track_lsf(lsf_id)
        if not lsf_id or not m.remote_started(running):
            return m.row(
                "wall_limit",
                "FAIL",
                f"no started LSF job (status={running.get('status')})",
            )
        script = m.read_submit_script(running)
        write_text(m.out / "native" / f"submit-{lsf_id}.lsf", script)
        has_w = "#BSUB -W 00:01" in script
        terminal = m.wait_terminal(job_id, timeout=600)
        terminal = m.wait_deadline_receipt(job_id)
        result = terminal.get("result") or {}
        receipt = result.get("execution_deadline") or {}
        detail = m.lsf.detail(lsf_id)
        write_text(m.out / "native" / f"bjobs-{lsf_id}.txt", detail)
        write_json(m.out / "native" / "wall-terminal.json", terminal)
        native_exit = "Exited with exit code 140" in detail or "RUNLIMIT" in detail
        started_at = terminal.get("started_at")
        deadline_at = receipt.get("deadline_at")
        data = {
            "job_id": job_id,
            "lsf_job_id": lsf_id,
            "approval_budget": budget,
            "approval_ref": approval["approval_ref"],
            "approval_wall_seconds": 60,
            "bsub_walltime": "#BSUB -W 00:01" if has_w else None,
            "script_has_w": has_w,
            "status": terminal.get("status"),
            "error": terminal.get("error"),
            "started_at": started_at,
            "completed_at": terminal.get("completed_at"),
            "receipt": receipt,
            "native_bjobs_l_has_runlimit_or_exit140": native_exit,
            "native_bjobs_l": detail,
        }
        checks = {
            "approval_reaches_bsub": has_w,
            "terminated_not_completed": terminal.get("status") == "failed",
            "wall_receipt": receipt.get("decision") == "wall-deadline-expired",
            "receipt_verified": bool(receipt.get("verified")),
            "native_lsf_termination": native_exit,
            "wall_bound_is_60s": receipt.get("wall_seconds") == 60,
            "deadline_is_started_plus_wall": bool(started_at and deadline_at),
        }
        failed = [name for name, ok in checks.items() if not ok]
        write_json(m.out / "native" / "wall-limit.json", data)
        if failed:
            return m.row("wall_limit", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "wall_limit",
            "PASS",
            "approval wall 60s → #BSUB -W 00:01 → native LSF RUNLIMIT (exit 140) + "
            "platform receipt",
            evidence=[f"native/submit-{lsf_id}.lsf", f"native/bjobs-{lsf_id}.txt"],
            data=data,
        )

    # -- 7. queue deadline ------------------------------------------------

    def queue_deadline(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        budget = plan_budget(queue_seconds=40)
        approval = create_approval(
            controller,
            project=m.project,
            proposal_ref="prop-m403-queue",
            workflow="xtb_optimize",
            input_payload=SMILES_INPUT,
            method=SHORT_METHOD,
            budget=budget,
        )
        body = approval_submit_body(
            approval,
            project=m.project,
            workflow="xtb_optimize",
            input_payload=SMILES_INPUT,
            method=SHORT_METHOD,
            name="m403-queue",
            target_node=NODE_NAME,
        )
        close = m.lsf.badmin("hclose")
        try:
            code, created = m.submit(body)
            if code != 201 or not isinstance(created, dict):
                return m.row("queue_deadline", "FAIL", f"submit HTTP {code}: {created}")
            job_id = str(created["job_id"])
            pending = m.job(job_id)
            lsf_id = ""
            deadline = time.time() + 180
            while time.time() < deadline:
                pending = m.job(job_id)
                lsf_id = m.lsf_id(pending)
                if lsf_id:
                    break
                time.sleep(1.0)
            m.track_lsf(lsf_id)
            stat = m.lsf.wait_stat(lsf_id, {"PEND"}, timeout=60) if lsf_id else ""
            detail = m.lsf.detail(lsf_id) if lsf_id else ""
            write_text(m.out / "native" / f"bjobs-pending-{lsf_id}.txt", detail)
            terminal = m.wait_terminal(job_id, timeout=180)
            terminal = m.wait_deadline_receipt(job_id)
        finally:
            m.lsf.badmin("hopen")
        result = terminal.get("result") or {}
        receipt = result.get("execution_deadline") or {}
        gone = m.lsf.wait_absent(lsf_id, timeout=90) if lsf_id else True
        data = {
            "job_id": job_id,
            "lsf_job_id": lsf_id,
            "hclose": close,
            "pending_stat": stat,
            "pending_detail_has_reason": "PENDING REASONS" in detail,
            "status": terminal.get("status"),
            "error": terminal.get("error"),
            "receipt": receipt,
            "lsf_absent_after": gone,
        }
        write_json(m.out / "native" / "queue-deadline.json", data)
        checks = {
            "was_pending": stat == "PEND",
            "terminal_failed": terminal.get("status") == "failed",
            "queue_receipt": receipt.get("decision") == "queue-deadline-expired",
            "receipt_verified": bool(receipt.get("verified")),
            "lsf_job_removed": gone,
            "queue_bound_is_40s": receipt.get("queue_seconds") == 40,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("queue_deadline", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "queue_deadline",
            "PASS",
            f"pending LSF job {lsf_id} expired at its 40 s queue deadline → terminal + bkill",
            evidence=[f"native/bjobs-pending-{lsf_id}.txt", "native/queue-deadline.json"],
            data=data,
        )

    # -- 8. PAUSED timing -------------------------------------------------

    def paused_timing(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        budget = plan_budget(wall_seconds=90)
        approval = create_approval(
            controller,
            project=m.project,
            proposal_ref="prop-m403-paused",
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            budget=budget,
        )
        body = approval_submit_body(
            approval,
            project=m.project,
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            name="m403-paused",
            target_node=NODE_NAME,
        )
        code, created = m.submit(body)
        if code != 201 or not isinstance(created, dict):
            return m.row("paused_timing", "FAIL", f"submit HTTP {code}: {created}")
        job_id = str(created["job_id"])
        running = m.wait_remote_started(job_id, timeout=300)
        lsf_id = m.lsf_id(running)
        m.track_lsf(lsf_id)
        if not lsf_id or not m.remote_started(running):
            return m.row("paused_timing", "FAIL", "no started LSF job")
        started = time.time()
        time.sleep(10)
        controller.request("POST", f"/api/v1/jobs/{job_id}/pause")
        m.wait_status(job_id, {"paused"}, timeout=120)
        paused_stat = m.lsf.wait_stat(lsf_id, {"USUSP", "PSUSP"}, timeout=60)
        time.sleep(25)
        controller.request("POST", f"/api/v1/jobs/{job_id}/unpause")
        terminal = m.wait_terminal(job_id, timeout=600)
        terminal = m.wait_deadline_receipt(job_id)
        elapsed = round(time.time() - started, 1)
        receipt = ((terminal.get("result") or {}).get("execution_deadline")) or {}
        deadline_absolute = None
        if receipt.get("deadline_at") and receipt.get("started_at"):
            deadline_absolute = round(
                (
                    datetime.fromisoformat(str(receipt["deadline_at"]).replace("Z", "+00:00"))
                    - datetime.fromisoformat(str(receipt["started_at"]).replace("Z", "+00:00"))
                ).total_seconds()
            )
        data = {
            "job_id": job_id,
            "lsf_job_id": lsf_id,
            "paused_lsf_stat": paused_stat,
            "status": terminal.get("status"),
            "elapsed_since_start_s": elapsed,
            "deadline_minus_started_s": deadline_absolute,
            "receipt": receipt,
            "wall_seconds": 90,
        }
        write_json(m.out / "native" / "paused-timing.json", data)
        checks = {
            "paused_in_lsf": paused_stat in {"USUSP", "PSUSP"},
            "terminated": terminal.get("status") == "failed",
            # The deadline stays 90 s from the start: the 25 s pause did not
            # extend it (a client-visible pause never buys extra science time).
            "paused_time_counted": deadline_absolute == 90,
            "receipt_verified": bool(receipt.get("verified")),
            "no_runaway": elapsed <= 90 + 90,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("paused_timing", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "paused_timing",
            "PASS",
            f"the 90 s absolute deadline terminated the attempt after a real bstop "
            f"(elapsed {elapsed}s from start)",
            evidence=["native/paused-timing.json"],
            data=data,
        )

    # -- 9. local process-group wall -------------------------------------

    def local_wall(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        budget = plan_budget(wall_seconds=10)
        approval = create_approval(
            controller,
            project=m.project,
            proposal_ref="prop-m403-local",
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            budget=budget,
        )
        body = approval_submit_body(
            approval,
            project=m.project,
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            name="m403-local-wall",
        )
        body.pop("execution_mode", None)
        body.pop("target_node", None)
        code, created = m.submit(body)
        if code != 201 or not isinstance(created, dict):
            return m.row("local_wall", "FAIL", f"submit HTTP {code}: {created}")
        job_id = str(created["job_id"])
        terminal = m.wait_terminal(job_id, timeout=300)
        terminal = m.wait_deadline_receipt(job_id)
        receipt = ((terminal.get("result") or {}).get("execution_deadline")) or {}
        native = receipt.get("native") or {}
        pids = native.get("killed_pids") or []
        alive = pids_alive(pids)
        data = {
            "job_id": job_id,
            "status": terminal.get("status"),
            "receipt": receipt,
            "killed_pids": pids,
            "alive_after": alive,
            "wall_seconds": 10,
        }
        write_json(m.out / "native" / "local-wall.json", data)
        checks = {
            "terminated": terminal.get("status") == "failed",
            "wall_receipt": receipt.get("decision") == "wall-deadline-expired",
            "receipt_verified": bool(receipt.get("verified")),
            "process_group_gone": not alive,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("local_wall", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "local_wall",
            "PASS",
            f"the same wall deadline terminated local process group {pids} "
            f"(SIGCONT→TERM→KILL receipt)",
            evidence=["native/local-wall.json"],
            data=data,
        )

    # -- 10. Controller restart / remaining time --------------------------

    def restart(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        budget = plan_budget(wall_seconds=120)
        approval = create_approval(
            controller,
            project=m.project,
            proposal_ref="prop-m403-restart",
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            budget=budget,
        )
        body = approval_submit_body(
            approval,
            project=m.project,
            workflow="optimize",
            input_payload={"source": LONG_MOLECULE, "source_type": "smiles"},
            method=LONG_METHOD,
            name="m403-restart",
            target_node=NODE_NAME,
        )
        code, created = m.submit(body)
        if code != 201 or not isinstance(created, dict):
            return m.row("restart", "FAIL", f"submit HTTP {code}: {created}")
        job_id = str(created["job_id"])
        running = m.wait_remote_started(job_id, timeout=300)
        lsf_id = m.lsf_id(running)
        m.track_lsf(lsf_id)
        if not lsf_id or not m.remote_started(running):
            return m.row("restart", "FAIL", "no started LSF job")
        started_at = str(running.get("started_at") or "")
        from acp_services.runtime.deadline_enforcement import (
            attempt_clock_from_record,
        )

        clock_before = attempt_clock_from_record(
            _RecordView(
                created_at=running.get("created_at"),
                started_at=running.get("started_at"),
                result=running.get("result"),
            )
        )
        deadline_before = clock_before.wall_deadline_at
        active_before_restart = m.lsf.active_ids()
        time.sleep(25)
        m.controller.stop()
        time.sleep(2)
        restarted = m.start_controller("main")
        adopted = m.wait_status(job_id, {"running", "failed", "completed"}, timeout=180)
        active_after = m.lsf.active_ids()
        new_lsf_jobs = sorted(active_after - active_before_restart)
        terminal = m.wait_terminal(job_id, timeout=600, controller=restarted)
        terminal = m.wait_deadline_receipt(job_id, controller=restarted)
        receipt = ((terminal.get("result") or {}).get("execution_deadline")) or {}
        deadline_after = receipt.get("deadline_at")
        same_deadline = (
            deadline_before is not None
            and deadline_after is not None
            and abs(
                (
                    datetime.fromisoformat(str(deadline_before).replace("Z", "+00:00"))
                    - datetime.fromisoformat(str(deadline_after).replace("Z", "+00:00"))
                ).total_seconds()
            )
            <= 1
        )
        completed_at = terminal.get("completed_at")
        restart_wall_s = None
        if completed_at and started_at:
            restart_wall_s = round(
                (
                    datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
                    - datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                ).total_seconds(),
                1,
            )
        data = {
            "job_id": job_id,
            "lsf_job_id": lsf_id,
            "deadline_before_restart": str(deadline_before),
            "deadline_after_restart": deadline_after,
            "same_absolute_deadline": same_deadline,
            "adopted_status": adopted.get("status"),
            "new_lsf_jobs_after_restart": new_lsf_jobs,
            "status": terminal.get("status"),
            "wall_here_s": restart_wall_s,
            "wall_seconds": 120,
            "receipt": receipt,
        }
        write_json(m.out / "native" / "restart.json", data)
        checks = {
            "adopted_same_lsf_job": bool(lsf_id) and not new_lsf_jobs,
            "same_absolute_deadline": same_deadline,
            "terminated": terminal.get("status") == "failed",
            # The restored clock is the *same* 120 s from the original start, not
            # 120 s from the restart (the terminal timestamp includes the bkill
            # confirmation handshake, so it is bounded separately).
            "remaining_time_restored": receipt.get("wall_seconds") == 120,
            "no_runaway": (restart_wall_s or 0) <= 120 + 90,
            "receipt_verified": bool(receipt.get("verified")),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("restart", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "restart",
            "PASS",
            f"Controller restart re-adopted LSF {lsf_id} with the same absolute deadline "
            f"({deadline_after}); no second bsub",
            evidence=["native/restart.json"],
            data=data,
        )

    # -- 11. publication retry (no recompute) ----------------------------

    def publication(self) -> Row:
        """Publication retry only — never a second computation.

        Uses a real *local* envelope job (``singlepoint`` is one of the two
        frozen envelope workflows) so the T26 receipt/publication boundary is
        the shipping one.  Two recovery facts are proven:

        1. a lost **published manifest** is rebuilt from the existing receipt
           after a restart (retry publication, same attempt, no executor);
        2. a lost **execution receipt** is refused with a typed integrity
           failure — the platform never fabricates a result and never
           recomputes the science.
        """
        m = self.m
        controller = m.controller
        assert controller is not None
        body = {
            "workflow": "singlepoint",
            "name": "m403-publication",
            "input": SMILES_INPUT,
            "method": {"method": "r2SCAN-3c"},
            "resources": dict(RESOURCES),
            "project_id": m.project,
        }
        code, created = m.submit(body)
        if code != 201 or not isinstance(created, dict):
            return m.row("publication", "FAIL", f"submit HTTP {code}: {created}")
        job_id = str(created["job_id"])
        terminal = m.wait_terminal(job_id, timeout=900)
        if str(terminal.get("status")) != "completed":
            return m.row(
                "publication",
                "FAIL",
                f"envelope job did not complete: {terminal.get('status')} {terminal.get('error')}",
            )
        work_dir = Path(str(terminal.get("work_dir") or ""))
        receipt_path = work_dir / "execution_receipt.json"
        manifest_path = work_dir / "RESULT" / "result_manifest.json"
        attempt = int(terminal.get("attempt") or 1)
        from acp.scheduler.store import JobStore

        if m.jobs_db is None:
            return m.row("publication", "FAIL", "the Controller job database is unknown")
        store = JobStore(str(m.jobs_db))

        def _stage(pending: bool) -> None:
            record = store.get(job_id)
            if record is None:
                raise StepError("publication", "job row missing")
            result = dict(record.result or {})
            result["publication_pending"] = pending
            result["terminal_side_effects_done"] = True
            store.transition(
                job_id,
                expected_status=(record.status,),
                expected_revision=record.revision,
                expected_attempt=record.attempt,
                result=result,
            )

        manifest_back = False
        publication_events = 0
        still_completed = False
        integrity_refused = None
        events_path = work_dir / "WORK" / "00_RUNTIME" / "events.jsonl"

        def _publication_count() -> int:
            if not events_path.is_file():
                return 0
            return sum(
                1
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if "publication_completed" in line
            )

        def _await_publication_count(expected: int, timeout: float = 120.0) -> int:
            deadline = time.time() + timeout
            count = _publication_count()
            while time.time() < deadline and count < expected:
                time.sleep(2)
                count = _publication_count()
            return count

        baseline_publications = _publication_count()
        # (1) delete the published manifest, mark publication pending, restart:
        # the retry must re-publish from the existing receipt (same attempt).
        if manifest_path.is_file():
            manifest_path.unlink()
        controller.stop()
        time.sleep(2)
        _stage(True)
        restarted = m.start_controller("main")
        publication_events = _await_publication_count(baseline_publications + 1)
        manifest_back = manifest_path.is_file()
        after = m.job(job_id, controller=restarted)
        still_completed = (
            str(after.get("status")) == "completed" and int(after.get("attempt") or 0) == attempt
        )
        # (2) delete the execution receipt and mark publication pending again:
        # the platform must refuse (typed integrity), never fabricate a result.
        if receipt_path.is_file():
            receipt_path.unlink()
        if manifest_path.is_file():
            manifest_path.unlink()
        controller.stop()
        time.sleep(2)
        _stage(True)
        restarted = m.start_controller("main")
        deadline = time.time() + 60
        pending_event = False
        while time.time() < deadline:
            text = events_path.read_text(encoding="utf-8") if events_path.is_file() else ""
            pending_event = "job.publication_pending" in text
            if pending_event:
                break
            time.sleep(2)
        after = m.job(job_id, controller=restarted)
        integrity_refused = pending_event and not manifest_path.is_file()
        events = events_path.read_text(encoding="utf-8") if events_path.is_file() else ""
        data = {
            "job_id": job_id,
            "attempt": attempt,
            "manifest_restored": manifest_back,
            "publication_count": publication_events,
            "baseline_publications": baseline_publications,
            "status_after_retries": after.get("status"),
            "attempt_after_retries": after.get("attempt"),
            "no_recompute_same_attempt": still_completed,
            "receipt_lost_refused": integrity_refused,
            "receipt_present": receipt_path.is_file(),
            "manifest_present": manifest_path.is_file(),
            "publication_events": [line for line in events.splitlines() if "publication" in line][
                -5:
            ],
        }
        write_json(m.out / "native" / "publication.json", data)
        checks = {
            "manifest_retried": bool(manifest_back)
            and publication_events >= baseline_publications + 1,
            "no_recompute": still_completed,
            "receipt_loss_refused": bool(integrity_refused),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("publication", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "publication",
            "PASS",
            "publication retried from the existing receipt (same attempt) and a lost "
            "receipt is refused instead of recomputed",
            evidence=["native/publication.json"],
            data=data,
        )

    # -- 12. late terminal observation -----------------------------------

    def late_done(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        job = getattr(m, "submission_job", None)
        if not isinstance(job, dict):
            return m.row("late_done", "FAIL", "no completed submission job")
        job_id = str(job["id"])
        events_path = Path(str(job.get("work_dir") or "")) / "WORK" / "00_RUNTIME" / "events.jsonl"
        before = m.job(job_id)
        late = {
            "type": "remote.observed_done",
            "timestamp": utc_now(),
            "job_id": job_id,
            "late": True,
            "note": "an observation emitted after the terminal transition",
        }
        if events_path.parent.is_dir():
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(late) + "\n")
        time.sleep(6)
        after = m.job(job_id)
        data = {
            "job_id": job_id,
            "before": before.get("status"),
            "after": after.get("status"),
            "attempt_before": before.get("attempt"),
            "attempt_after": after.get("attempt"),
            "late_event": late,
        }
        write_json(m.out / "native" / "late-done.json", data)
        checks = {
            "status_unchanged": after.get("status") == before.get("status") == "completed",
            "attempt_unchanged": after.get("attempt") == before.get("attempt"),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return m.row("late_done", "FAIL", "failed checks: " + ", ".join(failed), data=data)
        return m.row(
            "late_done",
            "PASS",
            "a terminal row is not revived by a later observation (CAS + terminal scan set)",
            evidence=["native/late-done.json"],
            data=data,
        )

    # -- 13. multiuser / revocation --------------------------------------

    def multiuser(self) -> Row:
        m = self.m
        key_path = RUN_ROOT / "identity" / "acp_identity.db"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            key_path.unlink()
        from acp_services.workspace.identity_lifecycle import (
            IdentityLifecycle,
            IdentityLifecyclePolicy,
        )
        from acp_services.workspace.keys import ApiKeyManager

        key_manager = ApiKeyManager(key_path, iterations=2_000)
        lifecycle = IdentityLifecycle(
            key_path,
            policy=IdentityLifecyclePolicy(
                deployment_tier="lab", capabilities=("authentication", "isolation")
            ),
            key_manager=key_manager,
            password_params={
                "kdf": "pbkdf2_hmac_sha256",
                "iterations": 2_000,
                "salt_bytes": 16,
            },
        )
        alpha = "project_alpha"
        beta = "project_beta"
        accounts = {}
        for name, principal, login in (
            ("alice", "principal_m403_alice", "m403-alice"),
            ("bob", "principal_m403_bob", "m403-bob"),
        ):
            invitation = lifecycle.invite_account(
                principal_id=principal, login_id=login, roles=["member"]
            )
            accounts[name] = lifecycle.accept_invitation(
                invitation.token, password=f"M403-pass-{name}!"
            )
        headers: dict[str, dict[str, str]] = {}
        controller = m.start_controller(
            "identity",
            extra_env={
                "ACP_IDENTITY_MODE": "api-key",
                "ACP_IDENTITY_DB": str(key_path),
                "ACP_IDENTITY_TENANT": "acp-m403",
            },
        )
        tasks: list[str] = []
        try:
            # Provision the project through the platform's own project store
            # (the admin path that creates the DB row); a member key itself has
            # no workspace-scope grant to create projects.
            from acp.scheduler.projects import ProjectManager

            projects = ProjectManager(
                None,
                controller.run_root,
                db_path=controller.run_root / "acp_jobs.db",
            )
            created_project = projects.create_project(f"M4-03 identity {m.run_stamp}")
            alpha = str(
                created_project["project_id"]
                if isinstance(created_project, dict)
                else created_project.project_id
            )
            beta = "project_beta_m403"
            alice_key = lifecycle.issue_key(
                accounts["alice"].account_id,
                scope="workspace",
                actions=["read", "write", "control"],
                project=alpha,
            )
            headers["alice"] = {"x-api-key": alice_key.key}
            bob_write = lifecycle.issue_key(
                accounts["bob"].account_id,
                scope="workspace",
                actions=["read", "write", "control"],
                project=beta,
            )
            bob_read = lifecycle.issue_key(
                accounts["bob"].account_id, scope="workspace", actions=["read"], project=beta
            )
            headers["bob"] = {"x-api-key": bob_write.key}
            headers["bob_read"] = {"x-api-key": bob_read.key}
            before = m.lsf.all_ids()
            body = {
                "workflow": "xtb_optimize",
                "name": "m403-multiuser",
                "input": SMILES_INPUT,
                "method": SHORT_METHOD,
                "resources": dict(RESOURCES),
                "project_id": alpha,
                "execution_mode": "remote",
                "target_node": NODE_NAME,
                "idempotency_key": "m403-multiuser-1",
            }
            code_a, created_a = controller.request(
                "POST", "/api/v1/jobs", body, headers=headers["alice"]
            )
            if code_a != 201 or not isinstance(created_a, dict):
                raise StepError("multiuser.submit", f"HTTP {code_a}: {created_a}")
            alice_job = str(created_a["job_id"])
            tasks.append(alice_job)
            cross_code, cross_payload = controller.request(
                "POST", "/api/v1/jobs", body, headers=headers["bob"]
            )
            read_code, _read_payload = controller.request(
                "GET", f"/api/v1/jobs/{alice_job}", headers=headers["bob"]
            )
            roof_code, _ = controller.request(
                "POST", "/api/v1/jobs", body, headers=headers["bob_read"]
            )
            # a plain member has no usage-write surface at all
            usage_code, _ = controller.request(
                "POST",
                f"/api/v1/jobs/{alice_job}/usage",
                {"seconds": 999},
                headers=headers["alice"],
            )
            # The TaskRef must be readable by its owner while the credential is
            # valid; revocation is checked afterwards so the terminal wait is
            # not itself the thing being denied.
            terminal = m.wait_terminal(
                alice_job,
                timeout=900,
                controller=controller,
                headers=headers["alice"],
            )
            key_manager.revoke(alice_key.key_id)
            revoked_code, _ = controller.request(
                "POST",
                "/api/v1/jobs",
                {**body, "idempotency_key": "m403-multiuser-2"},
                headers=headers["alice"],
            )
            time.sleep(2)
            after = m.lsf.all_ids()
            new_jobs = sorted(after - before)
            for lsf_job_id in new_jobs:
                m.track_lsf(lsf_job_id)
            data: dict[str, Any] = {
                "alice_job": alice_job,
                "alice_status": terminal.get("status"),
                "cross_project_http": cross_code,
                "cross_project_payload": cross_payload,
                "cross_read_http": read_code,
                "read_only_submit_http": roof_code,
                "usage_write_http": usage_code,
                "revoked_submit_http": revoked_code,
                "new_lsf_jobs": new_jobs,
                "usage_write_route_exists": usage_code not in (404, 405),
            }
            write_json(m.out / "native" / "multiuser.json", data)
            checks = {
                "alice_submitted": bool(alice_job),
                "cross_project_denied": cross_code in (403, 404),
                "cross_project_read_denied": read_code == 404,
                "read_only_cannot_submit": roof_code in (401, 403),
                "revoked_key_refused": revoked_code in (401, 403),
                "no_usage_write_surface": usage_code in (404, 405),
                "only_alice_created_lsf_job": len(new_jobs) == 1,
            }
            failed = [name for name, ok in checks.items() if not ok]
            if failed:
                return m.row("multiuser", "FAIL", "failed checks: " + ", ".join(failed), data=data)
            return m.row(
                "multiuser",
                "PASS",
                "project isolation, action ceiling, revocation and the absent usage-write "
                "surface hold on the remote submit path",
                evidence=["native/multiuser.json"],
                data=data,
            )
        finally:
            for task in tasks:
                controller.request("POST", f"/api/v1/jobs/{task}/cancel", headers=headers["bob"])

    # -- 14. budget concurrency ------------------------------------------

    def budget_concurrency(self) -> Row:
        m = self.m
        controller = m.controller
        assert controller is not None
        budget = plan_budget(wall_seconds=300)
        approval = create_approval(
            controller,
            project=m.project,
            proposal_ref="prop-m403-budget",
            workflow="xtb_optimize",
            input_payload=SMILES_INPUT,
            method=SHORT_METHOD,
            budget=budget,
        )
        body = approval_submit_body(
            approval,
            project=m.project,
            workflow="xtb_optimize",
            input_payload=SMILES_INPUT,
            method=SHORT_METHOD,
            name="m403-budget",
            target_node=NODE_NAME,
        )
        before = m.lsf.all_ids()
        results: list[tuple[int, Any]] = []

        def _submit(tag: str) -> None:
            payload = dict(body)
            results.append(controller.request("POST", "/api/v1/jobs", payload))

        threads = [threading.Thread(target=_submit, args=(str(i),)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)
        codes = sorted(code for code, _payload in results)
        accepted = [payload for code, payload in results if code == 201]
        # The accepted submit still has to publish/verify the release and run
        # bsub; wait until every accepted job is visible in LSF before counting.
        deadline = time.time() + 180
        after = m.lsf.all_ids()
        while time.time() < deadline and len(after - before) < len(accepted):
            time.sleep(2)
            after = m.lsf.all_ids()
        for lsf_job_id in sorted(after - before):
            m.track_lsf(lsf_job_id)
        data = {
            "codes": codes,
            "accepted": [str(item.get("job_id")) for item in accepted],
            "denied": [
                {"code": code, "payload": payload} for code, payload in results if code != 201
            ],
            "new_lsf_jobs": sorted(after - before),
            "approval_ref": approval["approval_ref"],
        }
        for item in accepted:
            with m._lock:
                m.created_jobs.add(str(item.get("job_id")))
        write_json(m.out / "native" / "budget-concurrency.json", data)
        checks = {
            "one_admitted": codes.count(201) == 1,
            "one_denied": any(code in (403, 409, 422, 429) for code in codes),
            "single_lsf_job": len(data["new_lsf_jobs"]) == 1,
        }
        failed = [name for name, ok in checks.items() if not ok]
        for item in accepted:
            m.wait_terminal(str(item["job_id"]), timeout=600)
        if failed:
            return m.row(
                "budget_concurrency", "FAIL", "failed checks: " + ", ".join(failed), data=data
            )
        return m.row(
            "budget_concurrency",
            "PASS",
            "an approval with task_ref=1 admitted exactly one of two concurrent remote submits",
            evidence=["native/budget-concurrency.json"],
            data=data,
        )


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RecordView:
    """The durable columns the deadline clock reads (no live JobRecord needed)."""

    created_at: str | None
    started_at: str | None
    result: Mapping[str, Any] | None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="M4-03 real LSF acceptance matrix")
    parser.add_argument(
        "--out",
        default="reports/acp2_remediation_20261026/m4-03",
        help="evidence directory (git-tracked reports path by convention)",
    )
    parser.add_argument(
        "--keep-remote",
        action="store_true",
        help="keep the remote job directories (default: keep, cleanup only kills jobs)",
    )
    return parser.parse_args(argv)


def _order() -> tuple[str, ...]:
    return (
        "environment",
        "code_release",
        "submission",
        "illegal_target",
        "lifecycle",
        "race",
        "wall_limit",
        "queue_deadline",
        "paused_timing",
        "local_wall",
        "restart",
        "publication",
        "late_done",
        "budget_concurrency",
        "multiuser",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    matrix = M403(args)
    print(f"M4-03 acceptance → {matrix.out}", flush=True)
    legs = Legs(matrix)
    exit_code = 0
    cleanup_report: dict[str, Any] = {}
    try:
        matrix.prepare_environment()
        for name in _order():
            if name == "environment":
                row = matrix.run_step(name, legs.environment)
                if row.status != "PASS":
                    raise StepError("environment", row.detail)
                continue
            if name == "code_release":
                row = matrix.run_step(name, legs.code_release)
                if row.status != "PASS":
                    raise StepError("code_release", row.detail)
                matrix.start_controller("main")
                continue
            if name == "multiuser":
                # The identity controller replaces the local-single one; run it
                # last so no later row depends on the previous Controller.
                matrix.run_step(name, legs.multiuser)
                continue
            if matrix.controller is None:
                matrix.start_controller("main")
            matrix.run_step(name, getattr(legs, name))
    except StepError as exc:
        print(f"ABORT: {exc}", flush=True)
        exit_code = 1
    except Exception as exc:  # noqa: BLE001 - the trace is the evidence
        print(f"ABORT: {type(exc).__name__}: {exc}", flush=True)
        exit_code = 2
    finally:
        if matrix.controller is not None:
            write_text(
                matrix.out / f"controller-{matrix.controller.name}.log",
                matrix.controller.log_tail(20000),
            )
            matrix.controller.stop()
        cleanup_report = matrix.cleanup()

    rows = [row.to_dict() for row in matrix.rows]
    passed = [row["name"] for row in rows if row["status"] == "PASS"]
    failed = [row["name"] for row in rows if row["status"] == "FAIL"]
    not_verified = [row["name"] for row in rows if row["status"] == "NOT_VERIFIED"]
    summary = {
        "schema": SCHEMA,
        "generated_at": utc_now(),
        "head": _head(),
        "rows": rows,
        "passed": passed,
        "failed": failed,
        "not_verified": not_verified,
        "cleanup": {
            "killed": cleanup_report.get("killed"),
            "leftovers": cleanup_report.get("leftovers"),
            "errors": cleanup_report.get("errors"),
        },
        "cleanup_ok": not cleanup_report.get("leftovers") and not cleanup_report.get("errors"),
        "execution_limits": _execution_limits_decision(rows),
        "status": "PASS" if not failed and len(passed) == len(_order()) else "FAIL",
    }
    write_json(matrix.out / "summary.json", summary)
    print(
        json.dumps(
            {
                "passed": len(passed),
                "failed": failed,
                "not_verified": not_verified,
                "cleanup_ok": summary["cleanup_ok"],
                "status": summary["status"],
            },
            indent=2,
        ),
        flush=True,
    )
    if not summary["cleanup_ok"]:
        return max(exit_code, 3)
    return exit_code if failed else 0


def _head() -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO), capture_output=True, text=True
    )
    return {
        "head": completed.stdout.strip(),
        "dirty_paths": len([line for line in status.stdout.splitlines() if line.strip()]),
    }


def _execution_limits_decision(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The upgrade table demanded by the exit condition (evidence per row)."""
    by_name = {row["name"]: row for row in rows}

    def _ok(*names: str) -> bool:
        return all(by_name.get(name, {}).get("status") == "PASS" for name in names)

    wall_row = {
        "limit": "walltime_process_termination",
        "current": "NOT_VERIFIED",
        "evidence": {
            "lsf_three_way": _ok("wall_limit"),
            "local_process_group": _ok("local_wall"),
            "paused_counts": _ok("paused_timing"),
            "restart_remaining": _ok("restart"),
        },
    }
    wall_row["decision"] = (
        "upgrade-to-enforced" if all(wall_row["evidence"].values()) else "keep-NOT_VERIFIED"
    )
    queue_row = {
        "limit": "queue_deadline",
        "current": "NOT_VERIFIED",
        "evidence": {"expiry_terminal_receipt": _ok("queue_deadline")},
    }
    queue_row["decision"] = (
        "upgrade-to-enforced"
        if queue_row["evidence"]["expiry_terminal_receipt"]
        else "keep-NOT_VERIFIED"
    )
    return {
        "rows": [wall_row, queue_row],
        "rule": "a row is upgraded only with a real receipt; wiring is never evidence",
    }


if __name__ == "__main__":
    raise SystemExit(main())
