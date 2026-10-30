"""B04a Controller topology: one Controller, loopback client, CLI targeting.

The tests start one real Controller (uvicorn + the production composition root)
on an ephemeral loopback port and a temporary run root.  They never touch
production ``/var/lib/acp/runs`` or port 8765.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
CLIENT_PATH = SRC / "acp_services" / "entrypoints" / "controller_client.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _env(run_root: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ACP_RUN_ROOT": str(run_root),
            "ACP_HOST": "127.0.0.1",
            "ACP_PORT": str(port),
            "PYTHONPATH": os.pathsep.join([str(SRC), str(REPO_ROOT)]),
        }
    )
    return env


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _wait_for_terminal_job(
    controller: "LiveController", task_id: str, timeout: float = 60.0
) -> dict[str, Any]:
    """Wait (bounded) for the async fake workflow to reach a stable state.

    ``JobManager.submit`` returns after persisting the QUEUED row and starts
    execution on a background thread, so the Controller status endpoint and a
    direct local-store read can legitimately straddle a queued->starting or
    starting->terminal transition.  Query parity is a property of one task
    snapshot, not of two reads taken across a lifecycle transition; wait for
    the terminal snapshot before comparing the two query surfaces.
    """

    deadline = time.monotonic() + timeout
    last_status: str | None = None
    while time.monotonic() < deadline:
        job = _get_json(controller.url + f"/api/v1/jobs/{task_id}")
        status = job.get("status")
        last_status = status if isinstance(status, str) else None
        if last_status in _TERMINAL_STATUSES:
            return job
        time.sleep(0.05)
    raise AssertionError(
        f"job {task_id} did not reach a terminal state within {timeout}s "
        f"(last status: {last_status!r})"
    )


def _wait_ready(url: str, proc: subprocess.Popen[str], timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            raise AssertionError(f"Controller exited before ready: {stderr}")
        try:
            _get_json(url + "/api/health")
            return
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.1)
    raise AssertionError(f"Controller did not become ready at {url}")


def _start_controller(run_root: Path, port: int) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "acp.api.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=_env(run_root, port),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@dataclass
class LiveController:
    url: str
    port: int
    proc: subprocess.Popen[str]
    run_root: Path


@pytest.fixture(scope="module")
def controller(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveController]:
    run_root = tmp_path_factory.mktemp("acp2-controller-run-root")
    port = _free_port()
    proc = _start_controller(run_root, port)
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(url, proc)
        yield LiveController(url=url, port=port, proc=proc, run_root=run_root)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _client(controller: LiveController) -> Any:
    sys.path.insert(0, str(SRC))
    from acp_services.entrypoints.controller_client import ControllerClient

    return ControllerClient(controller.url)


def _run_cli(controller: LiveController, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "acp", *args],
        cwd=REPO_ROOT,
        env=_env(controller.run_root, controller.port),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _submit_fake(controller: LiveController, name: str) -> str:
    client = _client(controller)
    response = client.submit(
        {
            "workflow": "fake",
            "name": name,
            "input": {"source": "CCO", "source_type": "smiles"},
        }
    )
    task_id = response.get("job_id") or response.get("task_id")
    assert isinstance(task_id, str) and task_id
    return task_id


def test_single_controller_owns_the_run_root(controller: LiveController) -> None:
    lock_path = controller.run_root / ".manager.lock"
    assert lock_path.is_file()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["pid"] == controller.proc.pid
    assert lock["run_root"] == str(controller.run_root)


def test_client_read_only_surface(controller: LiveController) -> None:
    client = _client(controller)
    before = len(client.list_tasks(limit=1000).get("jobs", []))

    capabilities = client.capabilities()
    assert capabilities.schema_version == 1
    assert capabilities.workflows
    assert capabilities.limits
    assert capabilities.tools is not None
    assert set(capabilities.tools) == {
        "acp_capabilities",
        "acp_preflight",
        "acp_submit",
        "acp_status",
        "acp_cancel",
        "acp_prepare_artifact",
        "acp_read_artifact",
        "acp_analyze",
        "acp_get_guidance",
    }

    health = client.health()
    assert health.status == "ok"
    assert health.candidate_revision
    assert health.contract_version
    assert len(health.contract_digest) == 64
    assert health.contract_digest_scope == "delivered-contract-set"
    assert health.read_only is True

    task_id = _submit_fake(controller, "b04a-readonly")
    snapshot = client.status(task_id)
    assert snapshot.task_id == task_id
    assert snapshot.task_ref is not None
    assert snapshot.task_ref["task_id"] == task_id

    after = len(client.list_tasks(limit=1000).get("jobs", []))
    assert after == before + 1


def test_second_controller_refused_before_any_new_task(controller: LiveController) -> None:
    client = _client(controller)
    before = len(client.list_tasks(limit=1000).get("jobs", []))
    second_port = _free_port()
    second = _start_controller(controller.run_root, second_port)
    try:
        second.wait(timeout=45)
    finally:
        if second.poll() is None:
            second.kill()
            second.wait(timeout=10)
    stderr = second.stderr.read() if second.stderr is not None else ""
    assert second.returncode != 0
    assert "already owned by another ACP" in stderr
    after = len(client.list_tasks(limit=1000).get("jobs", []))
    assert after == before


def test_task_query_controller_matches_controller_and_local_store(
    controller: LiveController,
) -> None:
    task_id = _submit_fake(controller, "b04a-query-parity")
    controller_job = _wait_for_terminal_job(controller, task_id)

    remote = _run_cli(controller, "task", "query", "--controller", controller.url, "--job", task_id)
    assert remote.returncode == 0, remote.stderr
    remote_payload = json.loads(remote.stdout)
    assert remote_payload["status"] == "ok"
    assert remote_payload["job"] == controller_job

    local = _run_cli(
        controller,
        "task",
        "query",
        "--run-root",
        str(controller.run_root),
        "--job",
        task_id,
    )
    assert local.returncode == 0, local.stderr
    local_payload = json.loads(local.stdout)
    assert local_payload["status"] == "ok"
    assert local_payload["job"]["id"] == task_id
    assert local_payload["job"]["status"] == controller_job["status"]


def test_local_control_and_execute_refuse_a_live_controller(
    controller: LiveController,
) -> None:
    task_id = _submit_fake(controller, "b04a-lock-refusal")
    execute = _run_cli(
        controller,
        "task",
        "execute",
        "--run-root",
        str(controller.run_root),
        "--workflow",
        "fake",
        "--input",
        "CCO",
    )
    assert execute.returncode == 1
    payload = json.loads(execute.stdout)
    assert payload["reason"] == "controller_running_use_--controller"
    assert payload["controller_pid"] == controller.proc.pid

    pause = _run_cli(
        controller,
        "task",
        "pause",
        "--run-root",
        str(controller.run_root),
        "--job",
        task_id,
    )
    assert pause.returncode == 1
    assert json.loads(pause.stdout)["reason"] == "controller_running_use_--controller"

    query = _run_cli(
        controller,
        "task",
        "query",
        "--run-root",
        str(controller.run_root),
        "--job",
        task_id,
    )
    assert query.returncode == 0, query.stderr


def test_controller_client_does_not_import_acp_or_manage_jobs() -> None:
    source = CLIENT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name == "acp" or name.startswith("acp.") for name in imported)
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "JobManager" not in referenced
    assert "threading" not in imported
    assert "sqlite3" not in imported

    probe = (
        "import importlib.abc\n"
        "import sys\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "class Blocker(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'acp' or name.startswith('acp.'):\n"
        "            raise ImportError('acp import is forbidden in the client')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "import acp_services.entrypoints.controller_client as c\n"
        "print(c.ControllerClient.__name__)\n"
    )
    check = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert check.returncode == 0, check.stderr
