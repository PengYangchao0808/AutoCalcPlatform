"""
Phase 2 tests — verify LSF script generation, remote monitoring, and the
full RemoteJobRunner flow without a real compute node.

Uses mock SSH/SFTP (reusing the Phase 1 fakes) to test:
- build_remote_cli_command: CLI argv equivalent to _build_cmd
- generate_lsf_script: BSUB directives + PYTHONPATH + cd + echo exit
- derive_lsf_resources: nproc/mem parsing
- RemoteJobMonitor: bjobs status mapping, exit_code, tail, bkill
- RemoteJobRunner: full submit→monitor→finish, node selection, cancel,
  no-download verification, remote_execution stage task

Run with: PYTHONPATH=src python3 tests/test_remote_phase2.py
"""

from __future__ import annotations

import io
import posixpath
import stat
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.runner import (
    RemoteJobRunner,
    RemoteNodeUnavailableError,
)
from acp.scheduler.remote.script_gen import (
    LSFScriptSpec,
    build_lsf_script_spec,
    build_remote_cli_command,
    derive_lsf_resources,
    generate_lsf_script,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore

# ====================================================================== #
# Reuse Phase 1 mock infrastructure
# ====================================================================== #


class FakeSFTPFile(io.BytesIO):
    def __init__(self, data: bytes = b"", mode: str = "rb"):
        super().__init__(data if "b" in mode else b"")
        self.mode = mode

    def write(self, data):
        if "b" not in self.mode and isinstance(data, str):
            data = data.encode("utf-8")
        return super().write(data)

    def read(self, size=-1):
        raw = super().read(size)
        if "b" not in self.mode:
            return raw.decode("utf-8")
        return raw

    def close(self):
        pass


class FakeSFTP:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.attrs: dict[str, MagicMock] = {}
        self.dirs: set[str] = set()

    def put(self, localpath, remotepath):
        with open(localpath, "rb") as f:
            self.files[remotepath] = f.read()
        self._set_attr(remotepath, size=len(self.files[remotepath]))

    def get(self, remotepath, localpath):
        if remotepath not in self.files:
            raise FileNotFoundError(remotepath)
        with open(localpath, "wb") as f:
            f.write(self.files[remotepath])

    def file(self, remote_path, mode="r"):
        data = self.files.get(remote_path, b"")
        if "w" in mode or "a" in mode:
            f = FakeSFTPFile(b"", mode)
            original_path = remote_path

            def _on_close():
                self.files[original_path] = f.getvalue()
                self._set_attr(original_path, size=len(f.getvalue()))

            f.close = _on_close  # type: ignore[assignment]
            return f
        return FakeSFTPFile(data, mode)

    def stat(self, path):
        if path in self.dirs:
            a = MagicMock()
            a.st_size = 0
            a.st_mtime = 0.0
            a.st_mode = stat.S_IFDIR
            return a
        if path not in self.attrs and path not in self.files:
            raise FileNotFoundError(path)
        if path in self.files:
            # Always reflect current content size (content may be replaced between calls)
            return self._set_attr(path, size=len(self.files[path]))
        return self.attrs[path]

    def _set_attr(self, path, size=0, mtime=0.0, is_dir=False):
        a = MagicMock()
        a.st_size = size
        a.st_mtime = mtime
        a.st_mode = stat.S_IFDIR if is_dir else stat.S_IFREG
        self.attrs[path] = a
        return a

    def listdir(self, path):
        prefix = path.rstrip("/") + "/" if path != "/" else "/"
        names = []
        for fpath in list(self.files.keys()) | self.dirs:
            if fpath.startswith(prefix):
                rest = fpath[len(prefix) :]
                if "/" not in rest and rest:
                    names.append(rest)
        return names

    def mkdir(self, path):
        self.dirs.add(path)

    def remove(self, path):
        self.files.pop(path, None)
        self.attrs.pop(path, None)

    def close(self):
        pass


class FakeSSHClient:
    """Mock SSHClient with controllable per-command results."""

    def __init__(self, fake_sftp: FakeSFTP):
        self.fake_sftp = fake_sftp
        self.closed = False
        self._transport = MagicMock()
        self._transport.is_active.return_value = True
        # command -> (exit, stdout, stderr).  A default can be set.
        self.cmd_results: dict[str, tuple[int, str, str]] = {}
        # Optional: a function(cmd) -> (exit, stdout, stderr) for dynamic responses.
        self.cmd_handler = None

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        return self._transport

    def exec_command(self, command, timeout=None):
        if self.cmd_handler is not None:
            result = self.cmd_handler(command)
        else:
            result = self.cmd_results.get(command, (0, "", ""))
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = result[1].encode("utf-8")
        stderr.read.return_value = result[2].encode("utf-8")
        stdout.channel = MagicMock()
        stdout.channel.recv_exit_status.return_value = result[0]
        return stdin, stdout, stderr

    def open_sftp(self):
        return self.fake_sftp

    def close(self):
        self.closed = True


def make_pool_with_client(pool: SSHConnectionPool, node: RemoteNode, sftp: FakeSFTP):
    """Patch _create_client to return a single shared FakeSSHClient."""
    client = FakeSSHClient(sftp)

    def factory(n, timeout=30):
        return client

    return client, factory


def make_node(name="compute-01", **kw):
    defaults = dict(
        name=name,
        host="10.0.0.1",
        username="testuser",
        remote_work_dir="/scratch/test/acp_jobs",
        remote_code_dir="/home/test/acp_code",
        max_concurrent_jobs=3,
        host_key_policy="auto_add",
    )
    defaults.update(kw)
    return RemoteNode(**defaults)


# ====================================================================== #
# script_gen tests
# ====================================================================== #


def test_build_remote_cli_command_conformer():
    spec = JobSpec(
        workflow="conformer",
        name="ethanol",
        input={"source": "CCO", "source_type": "smiles"},
        method={"protocol": "ext"},
        resources={"nproc": 8, "mem": "16GB"},
    )
    cmd = build_remote_cli_command(spec)
    assert cmd[0:4] == ["python", "-m", "acp.cli", "run"]
    assert cmd[4] == "conformer"
    assert "--input" in cmd
    assert "inputs/input.xyz" in cmd
    assert "--output" in cmd
    assert "." in cmd
    assert "--protocol" in cmd
    assert "ext" in cmd
    assert "--name" in cmd
    assert "ethanol" in cmd
    assert "--nproc" in cmd
    assert "8" in cmd
    assert "--mem" in cmd
    print("  [OK] build_remote_cli_command: conformer workflow")


def test_build_remote_cli_command_nmr():
    spec = JobSpec(
        workflow="nmr",
        input={"source": "CCO"},
        method={"protocol": "giao", "backend": "orca"},
        resources={"nproc": 4},
    )
    cmd = build_remote_cli_command(spec)
    assert "run" in cmd and "nmr" in cmd
    assert "--protocol" in cmd and "giao" in cmd
    assert "--backend" in cmd and "orca" in cmd
    print("  [OK] build_remote_cli_command: nmr workflow")


def test_build_remote_cli_command_benchmark():
    spec = JobSpec(
        workflow="benchmark",
        input={"source": "CCO"},
        method={"benchmark_level": "fast", "protocols": "ext,censo-full"},
    )
    cmd = build_remote_cli_command(spec)
    assert "benchmark" in cmd
    assert "run" not in cmd  # benchmark uses `acp.cli benchmark`, not `run`
    assert "--benchmark-level" in cmd and "fast" in cmd
    assert "--protocols" in cmd
    print("  [OK] build_remote_cli_command: benchmark workflow")


def test_build_remote_cli_command_mechanism():
    spec = JobSpec(
        workflow="mechanism",
        input={"source": "CCO"},
        method={},
    )
    cmd = build_remote_cli_command(spec)
    assert "mechanism" in cmd
    assert "--output" in cmd
    # mechanism has no --protocol
    assert "--protocol" not in cmd
    print("  [OK] build_remote_cli_command: mechanism workflow")


def test_build_remote_cli_command_charge_mult():
    spec = JobSpec(
        workflow="conformer",
        input={"source": "CCO", "charge": 0, "multiplicity": 1},
    )
    cmd = build_remote_cli_command(spec)
    assert "--charge" in cmd and "0" in cmd
    assert "--multiplicity" in cmd and "1" in cmd
    print("  [OK] build_remote_cli_command: charge + multiplicity")


def test_build_remote_cli_command_invalid_workflow():
    spec = JobSpec(workflow="fake", input={"source": "x"})
    try:
        build_remote_cli_command(spec)
        assert False, "should raise"
    except ValueError as e:
        assert "fake" in str(e)
    print("  [OK] build_remote_cli_command: rejects unsupported workflow")


def test_build_remote_cli_command_no_config_path():
    """config_path must NOT be passed — it's a local server path, not remote."""
    spec = JobSpec(
        workflow="conformer",
        input={"source": "CCO"},
        config_path="/local/path/to/config.yaml",
    )
    cmd = build_remote_cli_command(spec)
    assert "--config" not in cmd, "config_path must not appear in remote CLI command"
    print("  [OK] build_remote_cli_command: config_path excluded (local-only path)")


def test_derive_lsf_resources():
    spec = JobSpec(
        workflow="conformer",
        input={"source": "CCO"},
        resources={"nproc": 16, "mem": "32GB"},
    )
    nproc, mem_per_core, queue, walltime, extra = derive_lsf_resources(
        spec, queue="gpu", walltime="12:00", extra_flags="-R span[hosts=1]"
    )
    assert nproc == 16
    assert mem_per_core == 2048  # 32768 MB / 16 = 2048
    assert queue == "gpu"
    assert walltime == "12:00"
    assert extra == "-R span[hosts=1]"
    print(f"  [OK] derive_lsf_resources: nproc={nproc}, mem_per_core={mem_per_core}")


def test_derive_lsf_resources_defaults():
    spec = JobSpec(workflow="conformer", input={"source": "CCO"})
    nproc, mem_per_core, _, _, _ = derive_lsf_resources(spec)
    assert nproc == 8
    assert mem_per_core == 2000
    print("  [OK] derive_lsf_resources: defaults when no resources")


def test_generate_lsf_script():
    lsf_spec = LSFScriptSpec(
        job_name="acp_test123",
        queue="normal",
        nproc=8,
        mem_mb_per_core=2000,
        walltime="24:00",
        remote_code_dir="/home/u/acp_code",
        remote_job_dir="/scratch/u/acp_jobs/test123",
        cli_command=["python", "-m", "acp.cli", "run", "conformer", "--input", "inputs/input.xyz"],
        extra_flags="-R span[hosts=1]",
    )
    script = generate_lsf_script(lsf_spec)
    assert script.startswith("#!/bin/bash")
    assert "#BSUB -J acp_test123" in script
    assert "#BSUB -q normal" in script
    assert "#BSUB -n 8" in script
    assert '#BSUB -R "rusage[mem=2000]"' in script
    assert "#BSUB -W 24:00" in script
    assert "/scratch/u/acp_jobs/test123/stdout.log" in script
    assert "#BSUB -R span[hosts=1]" in script
    assert 'PYTHONPATH="/home/u/acp_code/src:$PYTHONPATH"' in script
    assert 'cd "/scratch/u/acp_jobs/test123"' in script
    assert "python -m acp.cli run conformer" in script
    assert "echo $? > .exit_code" in script
    print("  [OK] generate_lsf_script: all BSUB directives + PYTHONPATH + cd + echo")


def test_build_lsf_script_spec_integration():
    node = make_node()
    spec = JobSpec(
        workflow="conformer",
        name="water",
        input={"source": "O", "source_type": "smiles"},
        method={"protocol": "ext"},
        resources={"nproc": 4, "mem": "8GB"},
    )
    lsf_spec, cli_cmd = build_lsf_script_spec(
        spec, "job_001", node, queue="normal", walltime="48:00"
    )
    assert lsf_spec.job_name == "acp_job_001"
    assert lsf_spec.nproc == 4
    assert lsf_spec.mem_mb_per_core == 2048  # 8192 / 4
    assert lsf_spec.remote_job_dir == "/scratch/test/acp_jobs/job_001"
    assert lsf_spec.remote_code_dir == "/home/test/acp_code"
    assert "conformer" in cli_cmd
    script = generate_lsf_script(lsf_spec)
    assert "#BSUB -W 48:00" in script
    print("  [OK] build_lsf_script_spec: integrated CLI + LSF generation")


# ====================================================================== #
# RemoteJobMonitor tests
# ====================================================================== #


def _make_monitor(node, sftp, cmd_handler=None):
    pool = SSHConnectionPool()
    client = FakeSSHClient(sftp)
    if cmd_handler:
        client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    stager = FileStager(pool)
    monitor = RemoteJobMonitor(pool, stager)
    return pool, stager, monitor, factory


def test_monitor_lsf_status_mapping():
    node = make_node()
    sftp = FakeSFTP()

    cases = [
        ("PEND", "pending"),
        ("RUN", "running"),
        ("DONE", "done"),
        ("EXIT", "failed"),
        ("UNKWN", "unknown"),
    ]
    for raw, expected in cases:
        pool, _stager, monitor, factory = _make_monitor(
            node, sftp, cmd_handler=lambda cmd, r=raw: (0, r + "\n", "")
        )
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            status = monitor.get_lsf_status(node, "12345")
        assert status == expected, f"bjobs={raw} -> {status}, expected {expected}"
        pool.close()
    print("  [OK] get_lsf_status: PEND/RUN/DONE/EXIT/UNKWN mapping")


def test_monitor_lsf_status_not_found():
    node = make_node()
    sftp = FakeSFTP()
    pool, _stager, monitor, factory = _make_monitor(
        node, sftp, cmd_handler=lambda cmd: (255, "Job <12345> not found\n", "")
    )
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        status = monitor.get_lsf_status(node, "12345")
    assert status == "not_found"
    pool.close()
    print("  [OK] get_lsf_status: 'not found' -> not_found")


def test_monitor_lsf_status_empty():
    node = make_node()
    sftp = FakeSFTP()
    pool, _stager, monitor, factory = _make_monitor(node, sftp, cmd_handler=lambda cmd: (0, "", ""))
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        status = monitor.get_lsf_status(node, "12345")
    assert status == "not_found"
    pool.close()
    print("  [OK] get_lsf_status: empty output -> not_found")


def test_monitor_get_exit_code():
    node = make_node()
    sftp = FakeSFTP()
    sftp.files["/scratch/test/job1/.exit_code"] = b"0\n"
    pool, stager, monitor, factory = _make_monitor(node, sftp)

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        code = monitor.get_exit_code(node, "/scratch/test/job1")
    assert code == 0
    pool.close()
    print("  [OK] get_exit_code: reads integer from .exit_code")


def test_monitor_get_exit_code_missing():
    node = make_node()
    sftp = FakeSFTP()
    pool, stager, monitor, factory = _make_monitor(node, sftp)
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        code = monitor.get_exit_code(node, "/scratch/test/job1")
    assert code is None
    pool.close()
    print("  [OK] get_exit_code: None when file missing")


def test_monitor_get_exit_code_nonzero():
    node = make_node()
    sftp = FakeSFTP()
    sftp.files["/scratch/test/job1/.exit_code"] = b"42"
    pool, stager, monitor, factory = _make_monitor(node, sftp)
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        code = monitor.get_exit_code(node, "/scratch/test/job1")
    assert code == 42
    pool.close()
    print("  [OK] get_exit_code: non-zero exit codes read correctly")


def test_monitor_tail_stdout():
    node = make_node()
    sftp = FakeSFTP()
    sftp.files["/scratch/test/job1/stdout.log"] = b"line1\nline2\n"
    sftp._set_attr("/scratch/test/job1/stdout.log", size=12)
    pool, stager, monitor, factory = _make_monitor(node, sftp)
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        text, off = monitor.tail_stdout(node, "/scratch/test/job1", offset=0)
    assert text == "line1\nline2\n"
    assert off == 12
    pool.close()
    print("  [OK] tail_stdout: incremental read")


def test_monitor_running_job_count():
    node = make_node()
    sftp = FakeSFTP()
    pool, stager, monitor, factory = _make_monitor(
        node, sftp, cmd_handler=lambda cmd: (0, "3\n", "")
    )
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        count = monitor.get_running_job_count(node)
    assert count == 3
    pool.close()
    print("  [OK] get_running_job_count: parses count")


def test_monitor_disk_usage():
    node = make_node()
    sftp = FakeSFTP()
    pool, stager, monitor, factory = _make_monitor(
        node, sftp, cmd_handler=lambda cmd: (0, "45%\n", "")
    )
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        pct = monitor.check_disk_usage(node, "/scratch/test")
    assert pct == 45
    pool.close()
    print("  [OK] check_disk_usage: parses percentage")


def test_monitor_cancel_job_success():
    node = make_node()
    sftp = FakeSFTP()
    pool, stager, monitor, factory = _make_monitor(
        node, sftp, cmd_handler=lambda cmd: (0, "Job <123> is being terminated\n", "")
    )
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        ok = monitor.cancel_job(node, "123")
    assert ok is True
    pool.close()
    print("  [OK] cancel_job: bkill exit 0 -> True")


def test_monitor_cancel_job_failure():
    node = make_node()
    sftp = FakeSFTP()
    pool, stager, monitor, factory = _make_monitor(
        node, sftp, cmd_handler=lambda cmd: (1, "Job <123>: Not found\n", "")
    )
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        ok = monitor.cancel_job(node, "123")
    assert ok is False  # non-zero exit -> False, not raise
    pool.close()
    print("  [OK] cancel_job: bkill non-zero -> False (no raise)")


def test_monitor_is_terminal():
    assert RemoteJobMonitor.is_terminal("done") is True
    assert RemoteJobMonitor.is_terminal("failed") is True
    assert RemoteJobMonitor.is_terminal("not_found") is True
    assert RemoteJobMonitor.is_terminal("running") is False
    assert RemoteJobMonitor.is_terminal("pending") is False
    print("  [OK] is_terminal: done/failed/not_found=True, running/pending=False")


# ====================================================================== #
# RemoteJobRunner tests
# ====================================================================== #


def test_runner_select_node_least_loaded():
    node_a = make_node("node-a", max_concurrent_jobs=5)
    node_b = make_node("node-b", host="10.0.0.2", max_concurrent_jobs=5)
    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node_a, node_b])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)

    # node-a has 1 running, node-b has 3 running → pick node-a
    def handler(cmd):
        if "grep -c RUN" in cmd:
            if "node-a" in str(client._current_node) or True:
                # We can't distinguish per-node here; use the command context.
                pass
        return (0, "1\n", "")

    runner = RemoteJobRunner(
        pool, config, stager=FileStager(pool), monitor=MagicMock(), code_syncer=MagicMock()
    )
    # Mock get_running_job_count to return different values per node
    runner._monitor.get_running_job_count = lambda n: 1 if n.name == "node-a" else 3
    selected = runner.select_node(JobSpec(workflow="conformer", input={"source": "CCO"}))
    assert selected.name == "node-a"
    pool.close()
    print("  [OK] select_node: least-loaded (node-a=1 < node-b=3)")


def test_runner_select_node_no_enabled():
    node = make_node(enabled=False)
    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node])
    pool = SSHConnectionPool()
    runner = RemoteJobRunner(pool, config, monitor=MagicMock(), code_syncer=MagicMock())
    try:
        runner.select_node(JobSpec(workflow="conformer", input={"source": "CCO"}))
        assert False, "should raise"
    except RemoteNodeUnavailableError:
        pass
    pool.close()
    print("  [OK] select_node: raises when no enabled nodes")


def test_runner_select_node_all_at_capacity():
    node = make_node(max_concurrent_jobs=2)
    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node])
    pool = SSHConnectionPool()
    runner = RemoteJobRunner(pool, config, monitor=MagicMock(), code_syncer=MagicMock())
    runner._monitor.get_running_job_count = lambda n: 2  # at capacity
    try:
        runner.select_node(JobSpec(workflow="conformer", input={"source": "CCO"}))
        assert False, "should raise"
    except RemoteNodeUnavailableError:
        pass
    pool.close()
    print("  [OK] select_node: raises when all at capacity")


def test_runner_full_flow_success():
    """Full submit → monitor → finish, exit code 0, no files downloaded."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()

    # Track bsub, bjobs, bkill calls via a dynamic handler
    state = {"bsub_done": False, "polls": 0}

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            state["bsub_done"] = True
            return (0, "Job <54321> is submitted to queue <normal>.\n", "")
        if "bjobs" in cmd and "grep" not in cmd:
            state["polls"] += 1
            if state["polls"] <= 2:
                return (0, "RUN\n", "")
            # After 2 RUN polls, simulate completion: write .exit_code
            sftp.files[posixpath.join(node.remote_work_dir, "testjob", ".exit_code")] = b"0"
            return (0, "DONE\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        run_root = Path(tmp)
        work_dir = run_root / "proj" / "testjob"
        work_dir.mkdir(parents=True)

        spec = JobSpec(
            workflow="conformer",
            input={"source": "CCO", "source_type": "smiles"},
            method={"protocol": "ext"},
            resources={"nproc": 4},
        )
        record = JobRecord(id="testjob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel_event = threading.Event()

        runner = RemoteJobRunner(
            pool,
            config,
            stager=FileStager(pool),
            poll_interval=0,  # no sleep between polls
        )
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel_event)

        assert exit_code == 0
        assert state["bsub_done"] is True
        assert record.exit_code == 0
        assert record.result["lsf_job_id"] == "54321"
        assert record.result["node"] == "compute-01"
        assert record.result["remote_dir"] == posixpath.join(node.remote_work_dir, "testjob")
        assert "command_line" in record.result

        # Verify events were written
        events = event_log.read_all()
        event_types = [e["type"] for e in events]
        assert "remote.submitted" in event_types
        assert "job.completed" in event_types

        # Verify NO files were downloaded (no local results dir with QC output)
        local_files = list(work_dir.rglob("*"))
        local_names = [f.name for f in local_files if f.is_file()]
        assert "input.xyz" in local_names  # local staging of input
        assert "events.jsonl" in local_names
        assert not any(f.suffix in (".log", ".chk", ".out") for f in local_files if f.is_file()), (
            "No remote files should be downloaded"
        )

    pool.close()
    print(f"  [OK] full flow: exit={exit_code}, lsf=54321, events={len(events)}, no download")


def test_runner_full_flow_failure():
    """Remote job exits non-zero → runner returns non-zero, records error."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    polls = {"n": 0}

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            return (0, "Job <111> is submitted to queue <normal>.\n", "")
        if "bjobs" in cmd and "grep" not in cmd:
            polls["n"] += 1
            if polls["n"] <= 1:
                return (0, "RUN\n", "")
            sftp.files[posixpath.join(node.remote_work_dir, "failjob", ".exit_code")] = b"99"
            return (0, "EXIT\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "proj" / "failjob"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="conformer", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="failjob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel = threading.Event()

        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel)

        assert exit_code == 99
        assert record.result["exit_code"] == 99
        events = event_log.read_all()
        assert any(e["type"] == "job.failed" for e in events)

    pool.close()
    print(f"  [OK] failure flow: exit={exit_code}, job.failed event emitted")


def test_runner_cancel():
    """Cancellation triggers bkill and returns non-zero."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    bkill_called = {"yes": False}
    polls = {"n": 0}

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            return (0, "Job <777> is submitted to queue <normal>.\n", "")
        if "bjobs" in cmd and "grep" not in cmd:
            polls["n"] += 1
            return (0, "RUN\n", "")
        if "bkill" in cmd:
            bkill_called["yes"] = True
            # Simulate the LSF script writing .exit_code after bkill terminates the job
            sftp.files[posixpath.join(node.remote_work_dir, "canceljob", ".exit_code")] = b"130"
            return (0, "Job <777> is being terminated\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "proj" / "canceljob"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="conformer", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="canceljob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel = threading.Event()

        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)

        # Set cancel event immediately so the first poll cycle triggers it.
        cancel.set()
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel)

        assert bkill_called["yes"] is True
        assert exit_code != 0  # cancelled → non-zero
        events = event_log.read_all()
        assert any(e["type"] == "remote.cancel_sent" for e in events)

    pool.close()
    print(f"  [OK] cancel flow: bkill={bkill_called['yes']}, exit={exit_code}")


def test_runner_submission_failure():
    """bsub returns non-zero → RemoteSubmissionError → exit 1."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()

    def cmd_handler(cmd):
        if "bsub" in cmd:
            return (1, "", "Queue does not exist\n")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "proj" / "subfail"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="conformer", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="subfail", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel = threading.Event()

        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel)

    assert exit_code == 1
    assert record.error is not None
    assert "bsub" in record.error.lower() or "submission" in record.error.lower()
    pool.close()
    print(f"  [OK] submission failure: exit={exit_code}, error recorded")


def test_runner_bsub_no_job_id():
    """bsub succeeds but output has no Job <NNN> → RemoteSubmissionError."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()

    def cmd_handler(cmd):
        if "bsub" in cmd:
            return (0, "some garbage output\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "proj" / "badbsub"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="conformer", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="badbsub", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel = threading.Event()

        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel)

    assert exit_code == 1
    assert record.error is not None
    pool.close()
    print("  [OK] bsub no job ID: RemoteSubmissionError -> exit 1")


def test_runner_no_download():
    """Explicitly verify the runner never calls FileStager.download_file."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    polls = {"n": 0}

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            return (0, "Job <999> is submitted to queue <normal>.\n", "")
        if "bjobs" in cmd and "grep" not in cmd:
            polls["n"] += 1
            if polls["n"] > 1:
                sftp.files[posixpath.join(node.remote_work_dir, "ndjob", ".exit_code")] = b"0"
                return (0, "DONE\n", "")
            return (0, "RUN\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    stager = FileStager(pool)
    # Wrap download_file to detect any call.
    download_calls = []
    original_download = stager.download_file

    def spy_download(node_arg, remote_path, local_path):
        download_calls.append((str(remote_path), str(local_path)))
        return original_download(node_arg, remote_path, local_path)

    stager.download_file = spy_download  # type: ignore[assignment]

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "proj" / "ndjob"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="conformer", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="ndjob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel = threading.Event()

        runner = RemoteJobRunner(pool, config, stager=stager, poll_interval=0)
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel)

    assert exit_code == 0
    assert download_calls == [], f"download_file should never be called, got: {download_calls}"
    pool.close()
    print("  [OK] no download: download_file never called during full flow")


def test_runner_remote_execution_stage_task():
    """Verify a remote_execution stage task is created and transitions."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    polls = {"n": 0}

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            return (0, "Job <555> is submitted to queue <normal>.\n", "")
        if "bjobs" in cmd and "grep" not in cmd:
            polls["n"] += 1
            if polls["n"] > 2:
                sftp.files[posixpath.join(node.remote_work_dir, "stagejob", ".exit_code")] = b"0"
                return (0, "DONE\n", "")
            return (0, "RUN\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "proj" / "stagejob"
        work_dir.mkdir(parents=True)
        db_path = work_dir / ".stages.db"
        store = StageTaskStore(db_path)
        observer = StageTaskObserver(store)

        spec = JobSpec(workflow="conformer", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="stagejob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel = threading.Event()

        runner = RemoteJobRunner(
            pool,
            config,
            stager=FileStager(pool),
            stage_task_observer=observer,
            poll_interval=0,
        )
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel)

        assert exit_code == 0
        tasks = store.list_by_job("stagejob")
        stage_names = [t.stage_name for t in tasks]
        assert "remote_execution" in stage_names
        remote_task = [t for t in tasks if t.stage_name == "remote_execution"][0]
        assert remote_task.state == "completed"
        assert remote_task.exit_status == 0

    pool.close()
    print(f"  [OK] stage task: remote_execution created, state=completed, stages={stage_names}")


def test_runner_log_tailing():
    """stdout/stderr lines are emitted as log events."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    remote_dir = posixpath.join(node.remote_work_dir, "logjob")
    polls = {"n": 0}

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            # Seed the log files
            sftp.files[posixpath.join(remote_dir, "stdout.log")] = b"[CREST] starting\n"
            sftp.files[posixpath.join(remote_dir, "stderr.log")] = b""
            return (0, "Job <333> is submitted to queue <normal>.\n", "")
        if "bjobs" in cmd and "grep" not in cmd:
            polls["n"] += 1
            if polls["n"] == 2:
                # Append more to stdout
                sftp.files[posixpath.join(remote_dir, "stdout.log")] = (
                    b"[CREST] starting\n[CREST] conformer 1 done\n"
                )
                return (0, "RUN\n", "")
            if polls["n"] > 2:
                sftp.files[posixpath.join(remote_dir, ".exit_code")] = b"0"
                sftp.files[posixpath.join(remote_dir, "stdout.log")] = (
                    b"[CREST] starting\n[CREST] conformer 1 done\n[DFT] finished\n"
                )
                return (0, "DONE\n", "")
            return (0, "RUN\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        return (0, "", "")

    client = FakeSSHClient(sftp)
    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp) / "proj" / "logjob"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="conformer", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="logjob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel = threading.Event()

        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel)

        assert exit_code == 0
        events = event_log.read_all()
        log_events = [e for e in events if e.get("type") == "log"]
        stdout_lines = [e["line"] for e in log_events if e.get("stream") == "stdout"]
        # Should have captured the CREST/DFT lines
        assert any("[CREST] starting" in line for line in stdout_lines)
        assert any("[DFT] finished" in line for line in stdout_lines)

    pool.close()
    print(f"  [OK] log tailing: {len(stdout_lines)} stdout lines captured as events")


# ====================================================================== #
# Config (Phase 2 additions)
# ====================================================================== #


def test_remote_config_queue_walltime():
    cfg = RemoteExecutionConfig.from_config_dict(
        {
            "execution_mode": "remote",
            "queue": "gpu",
            "walltime": "72:00",
            "extra_flags": "-R span[hosts=1]",
            "nodes": [
                {
                    "name": "n",
                    "host": "h",
                    "username": "u",
                    "remote_work_dir": "/w",
                    "remote_code_dir": "/c",
                }
            ],
        }
    )
    assert cfg.queue == "gpu"
    assert cfg.walltime == "72:00"
    assert cfg.extra_flags == "-R span[hosts=1]"
    print("  [OK] RemoteExecutionConfig: queue/walltime/extra_flags parsed")


# ====================================================================== #


def main():
    tests = [
        # script_gen
        test_build_remote_cli_command_conformer,
        test_build_remote_cli_command_nmr,
        test_build_remote_cli_command_benchmark,
        test_build_remote_cli_command_mechanism,
        test_build_remote_cli_command_charge_mult,
        test_build_remote_cli_command_invalid_workflow,
        test_build_remote_cli_command_no_config_path,
        test_derive_lsf_resources,
        test_derive_lsf_resources_defaults,
        test_generate_lsf_script,
        test_build_lsf_script_spec_integration,
        # monitor
        test_monitor_lsf_status_mapping,
        test_monitor_lsf_status_not_found,
        test_monitor_lsf_status_empty,
        test_monitor_get_exit_code,
        test_monitor_get_exit_code_missing,
        test_monitor_get_exit_code_nonzero,
        test_monitor_tail_stdout,
        test_monitor_running_job_count,
        test_monitor_disk_usage,
        test_monitor_cancel_job_success,
        test_monitor_cancel_job_failure,
        test_monitor_is_terminal,
        # runner
        test_runner_select_node_least_loaded,
        test_runner_select_node_no_enabled,
        test_runner_select_node_all_at_capacity,
        test_runner_full_flow_success,
        test_runner_full_flow_failure,
        test_runner_cancel,
        test_runner_submission_failure,
        test_runner_bsub_no_job_id,
        test_runner_no_download,
        test_runner_remote_execution_stage_task,
        test_runner_log_tailing,
        # config
        test_remote_config_queue_walltime,
    ]
    failed = 0
    for t in tests:
        print(f"RUN  {t.__name__}")
        try:
            t()
        except Exception as e:
            failed += 1
            import traceback

            traceback.print_exc()
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{'=' * 60}")
    print(f"Results: {len(tests) - failed} passed, {failed} failed (of {len(tests)})")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
