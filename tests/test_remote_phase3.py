"""
Phase 3 tests — configuration & integration of remote execution.

Covers:
- Built-in + YAML cluster config defaults (execution_mode/poll_interval/retention_days/nodes)
- RemoteExecutionConfig: execution_mode validation, walltime_seconds, env-var dots
- RemoteNode: remote_work_dir no-spaces validation
- JobSpec.target_node field
- JobRunner._should_run_remote dispatch logic
- JobManager remote_config wiring (creates remote_runner, injects, shutdown closes pool)
- server._load_remote_config graceful degradation
- RemoteJobRunner: monitor timeout, started_at=None on init, cleanup on partial failure,
  task_id cache, cancel_event typing
- monitor.py shlex.quote in SSH commands
- sync.py atomic _save_state

Run with: PYTHONPATH=src python3 -m pytest tests/test_remote_phase3.py -v
"""

from __future__ import annotations

import io
import json
import shlex
import stat
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.manager import JobManager
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import (
    RemoteExecutionConfig,
    RemoteNode,
    _env_var_name,
    _parse_walltime,
)
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.runner import RemoteJobRunner
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.sync import CodeSyncer
from acp.scheduler.runner import JobRunner
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore

# ====================================================================== #
# Minimal mock infrastructure (mirrors Phase 2 fakes)
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
        if path not in self.files and path not in self.attrs:
            raise FileNotFoundError(path)
        if path in self.files:
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
    def __init__(self, fake_sftp: FakeSFTP):
        self.fake_sftp = fake_sftp
        self.closed = False
        self._transport = MagicMock()
        self._transport.is_active.return_value = True
        self.cmd_handler = None
        self.executed_commands: list[str] = []

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        return self._transport

    def exec_command(self, command, timeout=None):
        self.executed_commands.append(command)
        if self.cmd_handler is not None:
            result = self.cmd_handler(command)
        else:
            result = (0, "", "")
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


def make_node(name="compute-01", **kw):
    defaults = dict(
        name=name,
        host="10.0.0.1",
        username="testuser",
        remote_work_dir="/scratch/test/acp_jobs",
        remote_code_dir="/home/test/acp_code",
        max_concurrent_jobs=5,
        host_key_policy="auto_add",
    )
    defaults.update(kw)
    return RemoteNode(**defaults)


# ====================================================================== #
# Step 3.1-3.2: Cluster config defaults
# ====================================================================== #


def test_builtin_cluster_config_has_remote_fields():
    """The built-in default config must include the new remote-execution keys."""
    from conformer_search.config import _get_default_config

    cluster = _get_default_config()["cluster"]
    assert cluster["execution_mode"] == "local"
    assert cluster["poll_interval"] == 30
    assert cluster["retention_days"] == 180
    assert cluster["auto_sync"] is True
    assert cluster["nodes"] == []
    # Legacy keys preserved
    assert cluster["type"] == "local"
    assert cluster["queue"] == "normal"
    assert cluster["walltime"] == "24:00"


def test_defaults_yaml_has_remote_fields():
    """config/defaults.yaml must document the new keys."""
    yaml_path = Path(__file__).resolve().parent.parent / "config" / "defaults.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    assert "execution_mode: local" in text
    assert "poll_interval: 30" in text
    assert "retention_days: 180" in text
    assert "auto_sync: true" in text
    assert "nodes: []" in text


# ====================================================================== #
# Remote config validation (P2-10, env-var dots, P2-6, walltime)
# ====================================================================== #


def test_execution_mode_validation_rejects_typo():
    """P2-10: 'remot' should raise, not silently default to local."""
    with pytest.raises(ValueError, match="Invalid execution_mode"):
        RemoteExecutionConfig.from_config_dict({"execution_mode": "remot"})


def test_execution_mode_validation_accepts_local():
    cfg = RemoteExecutionConfig.from_config_dict({})
    assert cfg.execution_mode == "local"
    assert cfg.is_remote is False


def test_execution_mode_validation_accepts_remote():
    cfg = RemoteExecutionConfig.from_config_dict(
        {
            "execution_mode": "remote",
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
    assert cfg.is_remote is True


def test_env_var_name_handles_dots():
    """Node names with dots must map to valid env-var names."""
    assert _env_var_name("compute.01") == "ACP_REMOTE_PASSWORD_COMPUTE_01"
    assert _env_var_name("compute-01") == "ACP_REMOTE_PASSWORD_COMPUTE_01"
    assert _env_var_name("node a") == "ACP_REMOTE_PASSWORD_NODE_A"


def test_env_var_password_override_with_dots(monkeypatch):
    node = RemoteNode(
        name="compute.01",
        host="h",
        username="u",
        remote_work_dir="/w",
        remote_code_dir="/c",
    )
    monkeypatch.setenv("ACP_REMOTE_PASSWORD_COMPUTE_01", "secret")
    assert node.resolved_password() == "secret"


def test_remote_work_dir_rejects_spaces():
    """P2-6: spaces in remote_work_dir break BSUB directives."""
    with pytest.raises(ValueError, match="must not contain spaces"):
        RemoteNode.from_config_dict(
            {
                "name": "n",
                "host": "h",
                "username": "u",
                "remote_work_dir": "/path with space",
                "remote_code_dir": "/c",
            }
        )


def test_remote_code_dir_allows_spaces():
    """remote_code_dir is never in BSUB directives, spaces are tolerable."""
    node = RemoteNode.from_config_dict(
        {
            "name": "n",
            "host": "h",
            "username": "u",
            "remote_work_dir": "/w",
            "remote_code_dir": "/code dir",
        }
    )
    assert node.remote_code_dir == "/code dir"


def test_walltime_seconds_parsing():
    assert _parse_walltime("24:00") == 86400
    assert _parse_walltime("1:30:00") == 5400
    assert _parse_walltime("0:30") == 1800
    assert _parse_walltime("") == 0
    assert _parse_walltime("bad") == 0


def test_walltime_seconds_property():
    cfg = RemoteExecutionConfig(walltime="12:30")
    assert cfg.walltime_seconds == 45000
    cfg2 = RemoteExecutionConfig(walltime="bad")
    assert cfg2.walltime_seconds == 0


# ====================================================================== #
# Step 3.6: JobSpec.target_node
# ====================================================================== #


def test_jobspec_target_node_defaults_none():
    spec = JobSpec(workflow="ensemble", input={"source": "CCO"})
    assert spec.target_node is None


def test_jobspec_target_node_set():
    spec = JobSpec(workflow="ensemble", input={"source": "CCO"}, target_node="compute-02")
    assert spec.target_node == "compute-02"


def test_jobspec_to_dict_includes_target_node():
    spec = JobSpec(workflow="ensemble", input={"source": "CCO"}, target_node="node-x")
    d = spec.to_dict()
    assert d["target_node"] == "node-x"


# ====================================================================== #
# Step 3.3: JobRunner._should_run_remote
# ====================================================================== #


def test_should_run_remote_false_without_runner():
    runner = JobRunner()
    spec = JobSpec(workflow="ensemble", input={"source": "CCO"})
    assert runner._should_run_remote(spec) is False


def test_should_run_remote_false_for_fake():
    runner = JobRunner(remote_runner=MagicMock())
    spec = JobSpec(workflow="fake", input={"source": "CCO"})
    assert runner._should_run_remote(spec) is False


def test_should_run_remote_true_for_conformer_with_runner():
    runner = JobRunner(remote_runner=MagicMock())
    spec = JobSpec(workflow="ensemble", input={"source": "CCO"})
    assert runner._should_run_remote(spec) is True


def test_should_run_remote_true_for_nmr():
    runner = JobRunner(remote_runner=MagicMock())
    spec = JobSpec(workflow="nmr", input={"source": "CCO"})
    assert runner._should_run_remote(spec) is True


# ====================================================================== #
# Step 3.4: JobManager remote wiring
# ====================================================================== #


def test_manager_no_remote_config():
    """When remote_config is None, remote_runner stays None (local mode)."""
    with tempfile.TemporaryDirectory() as tmp:
        mgr = JobManager(run_root=Path(tmp), max_running=1)
        assert mgr.remote_runner is None
        assert mgr.runner.remote_runner is None
        mgr.shutdown()


def test_manager_local_config_no_runner():
    """execution_mode='local' should NOT create a remote runner."""
    cfg = RemoteExecutionConfig(execution_mode="local")
    with tempfile.TemporaryDirectory() as tmp:
        mgr = JobManager(run_root=Path(tmp), max_running=1, remote_config=cfg)
        assert mgr.remote_runner is None
        mgr.shutdown()


def test_manager_remote_config_creates_runner():
    """execution_mode='remote' with nodes should create + inject remote_runner."""
    node = make_node()
    cfg = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    with tempfile.TemporaryDirectory() as tmp:
        mgr = JobManager(run_root=Path(tmp), max_running=1, remote_config=cfg)
        assert mgr.remote_runner is not None
        assert mgr.runner.remote_runner is mgr.remote_runner
        assert mgr._runner_ssh_pool is not None
        mgr.shutdown()


def test_manager_shutdown_closes_ssh_pool():
    node = make_node()
    cfg = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    with tempfile.TemporaryDirectory() as tmp:
        mgr = JobManager(run_root=Path(tmp), max_running=1, remote_config=cfg)
        pool = mgr._runner_ssh_pool
        assert pool is not None
        mgr.shutdown()
        # After shutdown, the pool's internal dict is cleared.
        assert pool._pools == {}


# ====================================================================== #
# Step 3.5: server._load_remote_config graceful degradation
# ====================================================================== #


def test_load_remote_config_succeeds():
    """The server's _load_remote_config returns a valid RemoteExecutionConfig.

    The actual execution_mode depends on ~/.conformer_search.yaml; we only
    verify the function runs without error and returns a usable config.
    """
    try:
        from acp.api.server import _load_remote_config
    except ImportError:
        pytest.skip("fastapi not installed")
    cfg = _load_remote_config()
    assert cfg.execution_mode in ("local", "remote")
    assert isinstance(cfg.poll_interval, int)
    assert isinstance(cfg.nodes, list)


def test_load_remote_config_degrades_on_exception():
    """If load_config raises, we get a safe local-only config."""
    try:
        import acp.api.server as srv
    except ImportError:
        pytest.skip("fastapi not installed")
    with patch("conformer_search.config.load_config", side_effect=RuntimeError("boom")):
        cfg = srv._load_remote_config()
    assert cfg.execution_mode == "local"


# ====================================================================== #
# P1-3: Monitor loop timeout
# ====================================================================== #


def test_monitor_loop_times_out():
    """If LSF never reaches a terminal state, the loop exits after max_seconds."""
    node = make_node()
    config = RemoteExecutionConfig(
        execution_mode="remote",
        walltime="0:00",  # 0 seconds walltime
        auto_sync=False,
        nodes=[node],
    )
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            return (0, "Job <999> is submitted to queue <normal>.\n", "")
        if "bjobs" in cmd and "grep" not in cmd:
            return (0, "RUN\n", "")  # Never terminates
        if "grep -c" in cmd:
            return (0, "0\n", "")
        if "bkill" in cmd:
            return (0, "", "")
        return (0, "", "")

    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        run_root = Path(tmp)
        work_dir = run_root / "proj" / "timeoutjob"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="ensemble", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="timeoutjob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel_event = threading.Event()

        runner = RemoteJobRunner(
            pool,
            config,
            stager=FileStager(pool),
            poll_interval=0,
        )
        # Patch the timeout constants to make the test fast.
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            # Force a tiny max_seconds by patching the buffer constant
            import acp.scheduler.remote.runner as rmod

            with (
                patch.object(rmod, "_MONITOR_TIMEOUT_BUFFER", 0.5),
                patch.object(rmod, "_MONITOR_TIMEOUT_FALLBACK", 0.5),
            ):
                exit_code = runner.run(record, event_log, cancel_event)

    pool.close()
    assert exit_code != 0
    assert record.error is not None
    assert "timed out" in record.error.lower()


# ====================================================================== #
# P2-1: Cleanup remote dir on partial failure
# ====================================================================== #


def test_cleanup_on_submission_failure():
    """If bsub fails, the remote job directory should be cleaned up."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)

    cleaned = {"done": False}

    def cmd_handler(cmd):
        if "bsub" in cmd and "<" in cmd:
            # bsub fails — no job ID
            return (1, "bsub: some error\n", "")
        if "grep -c RUN" in cmd:
            return (0, "0\n", "")
        if "rm -rf" in cmd:
            cleaned["done"] = True
            return (0, "", "")
        return (0, "", "")

    client.cmd_handler = cmd_handler

    def factory(n, timeout=30):
        return client

    with tempfile.TemporaryDirectory() as tmp:
        run_root = Path(tmp)
        work_dir = run_root / "proj" / "failjob"
        work_dir.mkdir(parents=True)
        spec = JobSpec(workflow="ensemble", input={"source": "CCO", "source_type": "smiles"})
        record = JobRecord(id="failjob", spec=spec, work_dir=str(work_dir))
        event_log = JobEventLog(work_dir / "events.jsonl")
        cancel_event = threading.Event()

        runner = RemoteJobRunner(pool, config, stager=FileStager(pool), poll_interval=0)
        with patch.object(ssh_mod, "_create_client", side_effect=factory):
            exit_code = runner.run(record, event_log, cancel_event)

    pool.close()
    assert exit_code != 0
    assert cleaned["done"] is True, "rm -rf should have been called after bsub failure"


# ====================================================================== #
# P2-7: started_at=None on remote stage init
# ====================================================================== #


def test_remote_stage_started_at_none_on_init():
    """The remote_execution stage task should start with started_at=None."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()

    with tempfile.TemporaryDirectory() as tmp:
        store = StageTaskStore(Path(tmp) / "stages.db")
        observer = StageTaskObserver(store)
        runner = RemoteJobRunner(
            pool,
            config,
            stager=FileStager(pool),
            stage_task_observer=observer,
            poll_interval=0,
        )
        runner._init_remote_stage("job-x")
        tasks = store.list_by_job("job-x")
        remote_tasks = [t for t in tasks if t.stage_name == "remote_execution"]
        assert len(remote_tasks) == 1
        assert remote_tasks[0].started_at is None
        assert remote_tasks[0].state == "pending"
    pool.close()


# ====================================================================== #
# P2-8: task_id cache
# ====================================================================== #


def test_remote_stage_task_id_cached():
    """After _init_remote_stage, the task id should be cached for fast updates."""
    node = make_node()
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()

    with tempfile.TemporaryDirectory() as tmp:
        store = StageTaskStore(Path(tmp) / "stages.db")
        observer = StageTaskObserver(store)
        runner = RemoteJobRunner(
            pool,
            config,
            stager=FileStager(pool),
            stage_task_observer=observer,
            poll_interval=0,
        )
        runner._init_remote_stage("job-c")
        assert "job-c" in runner._remote_stage_task_ids
        cached_id = runner._remote_stage_task_ids["job-c"]

        # Update state — should use cache, not full scan
        runner._set_remote_stage_state("job-c", "running", started=True)
        task = store.get(cached_id)
        assert task.state == "running"
        assert task.started_at is not None
    pool.close()


# ====================================================================== #
# P2-9: cancel_event type annotation (threading.Event)
# ====================================================================== #


def test_runner_run_accepts_threading_event():
    """The run() signature must accept threading.Event (not just Any)."""
    import inspect

    sig = inspect.signature(RemoteJobRunner.run)
    cancel_param = sig.parameters["cancel_event"]
    # The annotation string should reference threading.Event, not Any.
    assert "threading.Event" in str(cancel_param.annotation) or "Event" in str(
        cancel_param.annotation
    )


# ====================================================================== #
# P2-5: shlex.quote in monitor SSH commands
# ====================================================================== #


def test_monitor_get_running_job_count_quotes_username():
    """The username must be shell-quoted in the bjobs command."""
    node = make_node(username="user; rm -rf /")
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)
    client.cmd_handler = lambda cmd: (0, "0\n", "")

    def factory(n, timeout=30):
        return client

    monitor = RemoteJobMonitor(pool, FileStager(pool))
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        monitor.get_running_job_count(node)

    pool.close()
    # The dangerous username must appear quoted in the executed command.
    assert any(
        "rm -rf" in shlex.quote("user; rm -rf /") in c or shlex.quote("user; rm -rf /") in c
        for c in client.executed_commands
    )


def test_monitor_check_disk_usage_quotes_path():
    node = make_node()
    pool = SSHConnectionPool()
    sftp = FakeSFTP()
    client = FakeSSHClient(sftp)
    client.cmd_handler = lambda cmd: (0, "45%\n", "")

    def factory(n, timeout=30):
        return client

    monitor = RemoteJobMonitor(pool, FileStager(pool))
    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        result = monitor.check_disk_usage(node, "/path with spaces")
    pool.close()
    assert result == 45
    # The path must be quoted (no raw space in the df command line)
    df_cmd = [c for c in client.executed_commands if c.startswith("df")]
    assert len(df_cmd) == 1
    assert "'/path with spaces'" in df_cmd[0] or '"/path with spaces"' in df_cmd[0]


# ====================================================================== #
# P2-12: sync._save_state atomic write
# ====================================================================== #


def test_sync_save_state_atomic():
    """_save_state should write via temp+rename (no partial files on success)."""
    node = make_node()
    pool = SSHConnectionPool()
    with tempfile.TemporaryDirectory() as tmp:
        syncer = CodeSyncer(pool, state_dir=Path(tmp))
        syncer._save_state(node, {"a.py": 1.0, "b.py": 2.0})
        state_file = syncer._state_file(node)
        # State file exists and is valid JSON
        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data == {"a.py": 1.0, "b.py": 2.0}
        # No leftover temp files
        leftovers = [p for p in Path(tmp).iterdir() if ".tmp" in p.name]
        assert leftovers == []
    pool.close()


def test_sync_save_state_survives_corruption():
    """Loading a corrupt state file should return empty dict, not crash."""
    node = make_node()
    pool = SSHConnectionPool()
    with tempfile.TemporaryDirectory() as tmp:
        syncer = CodeSyncer(pool, state_dir=Path(tmp))
        state_file = syncer._state_file(node)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{ invalid json !!!", encoding="utf-8")
        state = syncer._load_state(node)
        assert state == {}
    pool.close()


# ====================================================================== #
# P2-11: _ensure_remote_dir dedup (single source of truth)
# ====================================================================== #


def test_ensure_remote_dir_not_duplicated_in_sync():
    """sync.py must import _ensure_remote_dir from sftp.py, not define its own."""
    import acp.scheduler.remote.sftp as sftp_mod
    import acp.scheduler.remote.sync as sync_mod

    # The function object in sync's namespace should be the same as sftp's.
    assert sync_mod._ensure_remote_dir is sftp_mod._ensure_remote_dir


# ====================================================================== #
# Integration: remote config round-trips through YAML cluster section
# ====================================================================== #


def test_full_cluster_config_round_trip():
    """RemoteExecutionConfig.from_config_dict parses a realistic cluster block."""
    cluster_yaml = {
        "enabled": True,
        "execution_mode": "remote",
        "poll_interval": 15,
        "retention_days": 90,
        "auto_sync": True,
        "type": "lsf",
        "queue": "high",
        "walltime": "48:00",
        "extra_flags": "-R span[hosts=1]",
        "nodes": [
            {
                "name": "compute-01",
                "host": "10.16.5.157",
                "username": "<user>",
                "remote_work_dir": "/scratch/<user>/acp_jobs",
                "remote_code_dir": "/home/<user>/acp_code",
                "max_concurrent_jobs": 5,
            },
        ],
    }
    cfg = RemoteExecutionConfig.from_config_dict(cluster_yaml)
    assert cfg.execution_mode == "remote"
    assert cfg.poll_interval == 15
    assert cfg.retention_days == 90
    assert cfg.queue == "high"
    assert cfg.walltime == "48:00"
    assert cfg.walltime_seconds == 48 * 3600
    assert cfg.extra_flags == "-R span[hosts=1]"
    assert len(cfg.nodes) == 1
    assert cfg.nodes[0].name == "compute-01"
    assert cfg.is_remote is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
