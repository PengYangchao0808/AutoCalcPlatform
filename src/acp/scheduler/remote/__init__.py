"""
Remote Execution Support
========================

Infrastructure for dispatching ACP jobs to remote compute nodes over
SSH/SFTP.  This sub-package provides:

* :class:`RemoteNode` / :class:`RemoteExecutionConfig` — configuration models.
* :class:`SSHConnectionPool` — thread-safe, per-node connection pooling.
* :class:`FileStager` — SFTP upload / download / incremental log tail.
* :class:`CodeSyncer` — incremental source-code synchronisation.
* :func:`build_remote_cli_command` / :func:`generate_lsf_script` /
  :class:`LSFScriptSpec` — LSF script + CLI command generation.
* :class:`RemoteJobMonitor` — ``bjobs`` status polling + log tailing +
  ``bkill`` cancellation.
* :class:`RemoteJobRunner` — end-to-end remote job orchestrator.
* :class:`RemoteResultFetcher` — on-demand remote file/log retrieval.
* :class:`RemoteCleanup` — retention-based job-dir cleanup + pre-submit
  disk-pressure housekeeping.
* :class:`RemoteFileInfo` / :class:`SyncResult` — helper dataclasses.

Importing this package requires the ``paramiko`` extra
(``pip install -e '.[remote]'``).
"""

from __future__ import annotations

from acp.scheduler.remote.cleanup import (
    DEFAULT_MAX_DIRS_PER_SWEEP,
    DISK_CLEANUP_THRESHOLD,
    DISK_SKIP_THRESHOLD,
    CleanupReport,
    HousekeepingDecision,
    RemoteCleanup,
)
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.fetcher import (
    NotARemoteJobError,
    RemoteFileError,
    RemoteResultFetcher,
)
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.node_manager import (
    InterpreterProbe,
    NodeManager,
    NodeStatus,
    detect_node_python,
)
from acp.scheduler.remote.runner import (
    RemoteJobRunner,
    RemoteNodeUnavailableError,
    RemoteSubmissionError,
)
from acp.scheduler.remote.script_gen import (
    LSFScriptSpec,
    build_lsf_script_spec,
    build_remote_cli_command,
    derive_lsf_resources,
    generate_lsf_script,
)
from acp.scheduler.remote.sftp import FileStager, RemoteFileInfo
from acp.scheduler.remote.ssh import SSHConnectionPool, SSHExecutionError
from acp.scheduler.remote.sync import CodeSyncer, SyncResult

__all__ = [
    "CodeSyncer",
    "CleanupReport",
    "DEFAULT_MAX_DIRS_PER_SWEEP",
    "DISK_CLEANUP_THRESHOLD",
    "DISK_SKIP_THRESHOLD",
    "FileStager",
    "HousekeepingDecision",
    "InterpreterProbe",
    "LSFScriptSpec",
    "NodeManager",
    "NodeStatus",
    "NotARemoteJobError",
    "RemoteCleanup",
    "RemoteExecutionConfig",
    "RemoteFileError",
    "RemoteFileInfo",
    "RemoteJobMonitor",
    "RemoteJobRunner",
    "RemoteNode",
    "RemoteNodeUnavailableError",
    "RemoteResultFetcher",
    "RemoteSubmissionError",
    "SSHConnectionPool",
    "SSHExecutionError",
    "SyncResult",
    "build_lsf_script_spec",
    "build_remote_cli_command",
    "derive_lsf_resources",
    "detect_node_python",
    "generate_lsf_script",
]
