# ruff: noqa: F401 - this module is the package's re-export surface (__all__ is
# filtered for optional-extra availability, which the linter cannot evaluate).
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

Importing the remote *surface* requires the ``paramiko`` extra
(``pip install -e '.[remote]'``).  The package base (``config``,
``script_gen``, ``submission``) and the SFTP helpers stay importable without
it so a root ``acp`` install without ``[remote]`` can read remote configuration
and run ``acp doctor`` without crashing (M5-03 finding F4); names that need
paramiko are only present when the extra is installed.
"""

from __future__ import annotations

try:  # optional ``remote`` extra: never crash an install that omits paramiko
    import paramiko  # noqa: F401
except ImportError:  # pragma: no cover - exercised without ``pip install acp[remote]``
    from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode  # noqa: E402
    from acp.scheduler.remote.script_gen import (  # noqa: E402
        LSFScriptSpec,
        build_lsf_script_spec,
        build_remote_cli_command,
        derive_lsf_resources,
        generate_lsf_script,
    )
    from acp.scheduler.remote.submission import (  # noqa: E402
        build_owner_token,
        heartbeat_submit_worker,
        lease_ttl_seconds,
        register_submit_worker,
        release_submit_worker,
        submission_id_for,
        submission_lsf_name,
        submit_lease_valid,
    )
else:
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
        RemoteWriteError,
    )
    from acp.scheduler.remote.monitor import RemoteJobMonitor
    from acp.scheduler.remote.node_manager import (
        InterpreterProbe,
        NodeManager,
        NodeStatus,
        detect_node_python,
    )
    from acp.scheduler.remote.release import (
        PruneReport,
        ReleaseBinding,
        ReleaseError,
        ReleaseManifest,
        acquire_release_ref,
        build_release_manifest,
        ensure_node_release,
        prune_releases,
        release_release_ref,
        releases_root,
    )
    from acp.scheduler.remote.runner import (
        RemoteJobRunner,
        RemoteNodeUnavailableError,
        RemoteSubmissionError,
        RemoteSubmissionIndeterminate,
        RemoteSubmissionRejected,
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
    from acp.scheduler.remote.submission import (
        build_owner_token,
        heartbeat_submit_worker,
        lease_ttl_seconds,
        register_submit_worker,
        release_submit_worker,
        submission_id_for,
        submission_lsf_name,
        submit_lease_valid,
    )
    from acp.scheduler.remote.sync import CodeSyncer, SyncResult, build_sync_file_list

__all__ = [
    name
    for name in (
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
        "PruneReport",
        "RemoteCleanup",
        "RemoteExecutionConfig",
        "RemoteFileError",
        "RemoteFileInfo",
        "RemoteJobMonitor",
        "RemoteJobRunner",
        "RemoteNode",
        "RemoteNodeUnavailableError",
        "RemoteResultFetcher",
        "RemoteWriteError",
        "RemoteSubmissionError",
        "RemoteSubmissionIndeterminate",
        "RemoteSubmissionRejected",
        "ReleaseBinding",
        "ReleaseError",
        "ReleaseManifest",
        "SSHConnectionPool",
        "SSHExecutionError",
        "SyncResult",
        "acquire_release_ref",
        "build_lsf_script_spec",
        "build_owner_token",
        "build_remote_cli_command",
        "build_release_manifest",
        "build_sync_file_list",
        "derive_lsf_resources",
        "detect_node_python",
        "ensure_node_release",
        "generate_lsf_script",
        "heartbeat_submit_worker",
        "lease_ttl_seconds",
        "prune_releases",
        "register_submit_worker",
        "release_release_ref",
        "release_submit_worker",
        "releases_root",
        "submission_id_for",
        "submission_lsf_name",
        "submit_lease_valid",
    )
    if name in globals()
]
