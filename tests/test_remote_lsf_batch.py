"""
W-02 tests — batch ``bjobs`` status query + hot-poll wiring.

Covers:
- ``RemoteJobMonitor.get_lsf_status_many``: one query per chunk, strict row
  matching (mixed DONE/RUNNING/NOT_FOUND), no cross-job status borrowing.
- Single-job path keeps its phase-2 semantics (empty output / explicit
  "not found" → not_found; single-token output → mapped status).
- One-shot prefetch cache: consumed once, TTL respected.
- ``RemoteJobRunner.prefetch_lsf_statuses``: one query per node group,
  failures only log.
- ``JobManager._poll_round``: prefetch runs once per round *before* the
  per-job polls, only for remote records, and never breaks the round.

Run with: PYTHONPATH=src python3 -m pytest tests/test_remote_lsf_batch.py -v
"""

from __future__ import annotations

import io
import stat
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.runner import RemoteJobRunner
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool

# ====================================================================== #
# Minimal SSH/SFTP fakes (mirrors tests/test_remote_phase2.py)
# ====================================================================== #


class FakeSFTPFile(io.BytesIO):
    def __init__(self, data: bytes = b"", mode: str = "rb"):
        super().__init__(data if "b" in mode else b"")
        self.mode = mode

    def close(self):
        pass


class FakeSFTP:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()

    def stat(self, path):
        if path not in self.files and path not in self.dirs:
            raise FileNotFoundError(path)
        attr = MagicMock()
        attr.st_size = len(self.files.get(path, b""))
        attr.st_mtime = 0.0
        attr.st_mode = stat.S_IFDIR if path in self.dirs else stat.S_IFREG
        return attr

    def file(self, path, mode="r"):
        return FakeSFTPFile(self.files.get(path, b""), mode)

    def listdir(self, path):
        return []

    def close(self):
        pass


class FakeSSHClient:
    """Exec-command-only SSH client with a programmable response handler."""

    def __init__(self, handler=None):
        self.handler = handler
        self.executed_commands: list[str] = []
        self.closed = False
        self._transport = MagicMock()
        self._transport.is_active.return_value = True

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        return self._transport

    def exec_command(self, command, timeout=None):
        self.executed_commands.append(command)
        if self.handler is not None:
            code, out, err = self.handler(command)
        else:
            code, out, err = 0, "", ""
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = out.encode("utf-8")
        stderr.read.return_value = err.encode("utf-8")
        stdout.channel = MagicMock()
        stdout.channel.recv_exit_status.return_value = code
        return stdin, stdout, stderr

    def open_sftp(self):
        return FakeSFTP()

    def close(self):
        self.closed = True


def make_node(name: str = "compute-01", **kw) -> RemoteNode:
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


@contextmanager
def _monitor_with(handler, *, ttl: float = 30.0):
    """A monitor whose SSH pool always returns one fake client."""
    pool = SSHConnectionPool()
    client = FakeSSHClient(handler)
    monitor = RemoteJobMonitor(pool, FileStager(pool), status_cache_ttl=ttl)
    try:
        with patch.object(ssh_mod, "_create_client", return_value=client):
            yield monitor, client
    finally:
        pool.close()


def _query_ids(command: str) -> list[str]:
    """Extract the job ids of a ``bjobs <ids...> 2>&1`` command."""
    return command.replace("2>&1", "").split()[1:]


def _table(*rows: tuple[str, str]) -> str:
    header = "JOBID USER STAT QUEUE FROM_HOST EXEC_HOST JOB_NAME SUBMIT_TIME"
    lines = [header]
    for job_id, state in rows:
        lines.append(f"{job_id} testuser {state} normal hpc1 hpc2 job_{job_id} Nov 2 10:00")
    return "\n".join(lines) + "\n"


# ====================================================================== #
# get_lsf_status_many — strict row matching
# ====================================================================== #


def test_many_mixed_states_in_one_query():
    output = _table(("101", "DONE"), ("102", "RUN")) + "Job <103> is not found\n"

    def handler(command):
        assert len(_query_ids(command)) == 3
        return 0, output, ""

    with _monitor_with(handler) as (monitor, client):
        statuses = monitor.get_lsf_status_many(node=make_node(), lsf_job_ids=["101", "102", "103"])

    assert statuses == {"101": "done", "102": "running", "103": "not_found"}
    assert len(client.executed_commands) == 1, "three jobs must cost exactly one bjobs call"


def test_many_empty_ids_is_a_noop():
    with _monitor_with(lambda command: (0, "", "")) as (monitor, client):
        assert monitor.get_lsf_status_many(make_node(), []) == {}
    assert client.executed_commands == []


def test_many_never_borrows_another_jobs_status_code():
    """A ``DONE`` row of job 999 must NOT become job 101's status."""
    output = _table(("999", "DONE"))

    with _monitor_with(lambda command: (0, output, "")) as (monitor, client):
        statuses = monitor.get_lsf_status_many(make_node(), ["101"])
        single = monitor.get_lsf_status(make_node(), "101")

    assert statuses == {"101": "unknown"}
    assert single == "unknown", "single-job path must not scan any stat code either"
    assert len(client.executed_commands) == 2


def test_many_does_not_attribute_mixed_table_rows():
    """Same batch, two jobs, table order irrelevant — each id keeps its own row."""
    output = _table(("202", "EXIT"), ("201", "RUN"))

    with _monitor_with(lambda command: (0, output, "")) as (monitor, _client):
        statuses = monitor.get_lsf_status_many(make_node(), ["201", "202"])

    assert statuses == {"201": "running", "202": "failed"}


def test_many_explicit_not_found_only_names_requested_id():
    """``Job <303> is not found`` resolves 303; an unmatched id stays unknown."""
    output = _table(("301", "RUN")) + "Job <303> is not found\n"

    with _monitor_with(lambda command: (0, output, "")) as (monitor, _client):
        statuses = monitor.get_lsf_status_many(make_node(), ["301", "302", "303"])

    assert statuses == {"301": "running", "302": "unknown", "303": "not_found"}


def test_many_empty_output_stays_unknown():
    with _monitor_with(lambda command: (0, "", "")) as (monitor, _client):
        statuses = monitor.get_lsf_status_many(make_node(), ["401", "402"])
    assert statuses == {"401": "unknown", "402": "unknown"}


def test_many_chunks_at_most_96_ids_per_query():
    ids = [str(9000 + index) for index in range(100)]

    def handler(command):
        lines = "".join(f"Job <{job_id}> is not found\n" for job_id in _query_ids(command))
        return 0, lines, ""

    with _monitor_with(handler) as (monitor, client):
        statuses = monitor.get_lsf_status_many(make_node(), ids)

    assert len(client.executed_commands) == 2
    chunk_sizes = [len(_query_ids(command)) for command in client.executed_commands]
    assert chunk_sizes == [96, 4]
    assert set(statuses) == set(ids)
    assert set(statuses.values()) == {"not_found"}


# ====================================================================== #
# Single-job path keeps phase-2 semantics
# ====================================================================== #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PEND\n", "pending"),
        ("RUN\n", "running"),
        ("DONE\n", "done"),
        ("EXIT\n", "failed"),
        ("UNKWN\n", "unknown"),
    ],
)
def test_single_job_single_token_fallback(raw, expected):
    with _monitor_with(lambda command: (0, raw, "")) as (monitor, _client):
        assert monitor.get_lsf_status(make_node(), "12345") == expected


def test_single_job_not_found_and_empty_unchanged():
    cases = [("Job <12345> not found\n", "not_found"), ("", "not_found")]
    for raw, expected in cases:
        with _monitor_with(lambda command, r=raw: (0, r, "")) as (monitor, _client):
            assert monitor.get_lsf_status(make_node(), "12345") == expected


def test_single_job_row_beats_single_token_when_both_present():
    """A real table row for the requested id wins over a stray token line."""
    output = _table(("12345", "DONE"))
    with _monitor_with(lambda command: (0, output, "")) as (monitor, _client):
        assert monitor.get_lsf_status(make_node(), "12345") == "done"


# ====================================================================== #
# One-shot prefetch cache
# ====================================================================== #


def test_prefetch_cache_is_consumed_once():
    output = _table(("101", "DONE"), ("102", "RUN"))

    with _monitor_with(lambda command: (0, output, "")) as (monitor, client):
        prefetched = monitor.prefetch_statuses(make_node(), ["101", "102"])
        assert prefetched == {"101": "done", "102": "running"}
        assert len(client.executed_commands) == 1

        assert monitor.get_lsf_status(make_node(), "101") == "done"
        assert len(client.executed_commands) == 1, "fresh prefetch must serve the first poll"

        # One-shot: the entry was popped, so the next call queries the node.
        assert monitor.get_lsf_status(make_node(), "101") == "done"
        assert len(client.executed_commands) == 2


def test_prefetch_cache_respects_ttl():
    output = _table(("101", "DONE"))

    with _monitor_with(lambda command: (0, output, ""), ttl=0.0) as (monitor, client):
        monitor.prefetch_statuses(make_node(), ["101"])
        assert len(client.executed_commands) == 1
        # TTL expired (0 s): the cached value is dropped and a real query runs.
        assert monitor.get_lsf_status(make_node(), "101") == "done"
        assert len(client.executed_commands) == 2


# ====================================================================== #
# RemoteJobRunner.prefetch_lsf_statuses — one query per node group
# ====================================================================== #


def _record(
    job_id: str,
    *,
    node: str | None = None,
    lsf_job_id: str | None = None,
    status: JobStatus = JobStatus.RUNNING,
) -> JobRecord:
    result: dict[str, str] = {}
    if node is not None:
        result["node"] = node
    if lsf_job_id is not None:
        result["lsf_job_id"] = lsf_job_id
    return JobRecord(
        id=job_id,
        spec=JobSpec(workflow="fake", input={"source": "CCO"}),
        status=status,
        result=result or None,
    )


def test_runner_prefetch_groups_by_node():
    node_a = make_node("node-a")
    node_b = make_node("node-b", host="10.0.0.2")
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node_a, node_b])
    clients: dict[str, FakeSSHClient] = {}

    def handler(command):
        rows = [(job_id, "RUN") for job_id in _query_ids(command)]
        return 0, _table(*rows), ""

    def factory(node, timeout=30):
        return clients.setdefault(node.name, FakeSSHClient(handler))

    pool = SSHConnectionPool()
    runner = RemoteJobRunner(pool, config, stager=FileStager(pool))
    records = [
        _record("j1", node="node-a", lsf_job_id="101"),
        _record("j2", node="node-a", lsf_job_id="102"),
        _record("j3", node="node-b", lsf_job_id="201"),
        _record("j4", node="node-a"),  # no LSF id yet → skipped
        _record("j5", lsf_job_id="999"),  # no node → skipped
    ]

    with patch.object(ssh_mod, "_create_client", side_effect=factory):
        prefetched = runner.prefetch_lsf_statuses(records)

    assert prefetched == 3
    assert len(clients["node-a"].executed_commands) == 1
    assert _query_ids(clients["node-a"].executed_commands[0]) == ["101", "102"]
    assert len(clients["node-b"].executed_commands) == 1
    assert _query_ids(clients["node-b"].executed_commands[0]) == ["201"]
    pool.close()


def test_poll_remote_consumes_prefetched_status(tmp_path):
    """End-to-end hot path: one batch query serves the per-job poll (no re-query)."""
    node = make_node("node-a")
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()

    def handler(command):
        rows = [(job_id, "RUN") for job_id in _query_ids(command)]
        return 0, _table(*rows), ""

    client = FakeSSHClient(handler)
    runner = RemoteJobRunner(pool, config, stager=FileStager(pool))
    record = _record("j1", node="node-a", lsf_job_id="101")
    runner._job_states["j1"] = {
        "node": node,
        "remote_job_dir": "/scratch/test/acp_jobs/job_one",
        "lsf_job_id": "101",
        "stdout_offset": 0,
        "stderr_offset": 0,
        "poll_cycle": 0,
        "seen_stages": set(),
    }
    event_log = JobEventLog(tmp_path / "events.jsonl")

    with patch.object(ssh_mod, "_create_client", return_value=client):
        assert runner.prefetch_lsf_statuses([record]) == 1
        observation = runner.poll_remote(record, event_log, threading.Event())

    assert observation.terminal is False
    assert observation.lsf_status == "running"
    bjobs_calls = [c for c in client.executed_commands if c.startswith("bjobs ")]
    assert len(bjobs_calls) == 1, "prefetched status must be consumed, not queried again"
    pool.close()


def test_runner_prefetch_failure_only_logs():
    node = make_node("node-a")
    config = RemoteExecutionConfig(execution_mode="remote", auto_sync=False, nodes=[node])
    pool = SSHConnectionPool()
    runner = RemoteJobRunner(pool, config, stager=FileStager(pool))
    runner._monitor.prefetch_statuses = MagicMock(side_effect=RuntimeError("cluster down"))

    assert runner.prefetch_lsf_statuses([_record("j1", node="node-a", lsf_job_id="101")]) == 0
    pool.close()


# ====================================================================== #
# JobManager._poll_round — one prefetch per round, before the polls
# ====================================================================== #


class _RecordingRunner:
    def __init__(self, log: list, *, fail: bool = False) -> None:
        self.log = log
        self.fail = fail
        self.calls: list[list[str]] = []

    def prefetch_lsf_statuses(self, records):
        ids = [record.id for record in records]
        self.calls.append(ids)
        self.log.append(("prefetch", ids))
        if self.fail:
            raise RuntimeError("cluster down")
        return len(ids)


def _bare_manager(records_by_status: dict[str, list[JobRecord]], runner) -> JobManager:
    manager = JobManager.__new__(JobManager)
    manager._poll_stop = threading.Event()
    manager.remote_runner = runner
    manager.store = MagicMock()
    manager.store.list.side_effect = lambda status=None, limit=None: list(
        records_by_status.get(str(status), [])
    )
    manager._poll_job = lambda job_id: runner.log.append(("poll", job_id))
    manager._sweep_idempotency_staging = lambda: runner.log.append(("sweep", None))
    manager._dispatch_queued_jobs = lambda: runner.log.append(("dispatch", None))
    return manager


def test_poll_round_prefetches_remote_records_before_polling(monkeypatch):
    monkeypatch.delenv("ACP_LSF_BATCH_STATUS", raising=False)
    remote_a = _record("r1", node="node-a", lsf_job_id="101")
    remote_b = _record("r2", node="node-b", lsf_job_id="201")
    local = _record("l1")
    log: list = []
    runner = _RecordingRunner(log)
    manager = _bare_manager({"running": [remote_a, remote_b, local]}, runner)

    manager._poll_round()

    assert runner.calls == [["r1", "r2"]], "one prefetch for the whole round, remote only"
    assert log == [
        ("prefetch", ["r1", "r2"]),
        ("poll", "r1"),
        ("poll", "r2"),
        ("poll", "l1"),
        ("sweep", None),
        ("dispatch", None),
    ]


def test_poll_round_respects_disable_switch(monkeypatch):
    monkeypatch.setenv("ACP_LSF_BATCH_STATUS", "0")
    remote = _record("r1", node="node-a", lsf_job_id="101")
    log: list = []
    runner = _RecordingRunner(log)
    manager = _bare_manager({"running": [remote]}, runner)

    manager._poll_round()

    assert runner.calls == [], "ACP_LSF_BATCH_STATUS=0 must keep the per-job path"
    assert log == [("poll", "r1"), ("sweep", None), ("dispatch", None)]


def test_poll_round_survives_prefetch_failure():
    remote = _record("r1", node="node-a", lsf_job_id="101")
    log: list = []
    runner = _RecordingRunner(log, fail=True)
    manager = _bare_manager({"running": [remote]}, runner)

    manager._poll_round()  # must not raise

    assert log == [
        ("prefetch", ["r1"]),
        ("poll", "r1"),
        ("sweep", None),
        ("dispatch", None),
    ]


def test_poll_round_without_remote_runner_is_local_only():
    local = _record("l1")
    runner = _RecordingRunner([])
    manager = _bare_manager({"running": [local]}, runner)

    manager._poll_round()

    assert runner.log == [("poll", "l1"), ("sweep", None), ("dispatch", None)]
