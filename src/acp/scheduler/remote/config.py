"""
Remote Execution Configuration
==============================

Dataclasses describing remote compute nodes and the overall remote execution
policy. Parsed from the ``cluster`` section of the YAML configuration.

Author: QCcalc Team
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

__all__ = ["RemoteNode", "RemoteExecutionConfig"]


def _env_var_name(node_name: str) -> str:
    """Map a node name to its password env-var name.

    ``compute-01`` -> ``ACP_REMOTE_PASSWORD_COMPUTE_01``
    ``compute.01`` -> ``ACP_REMOTE_PASSWORD_COMPUTE_01`` (dots also replaced)
    """
    cleaned = node_name.upper().replace("-", "_").replace(" ", "_").replace(".", "_")
    return f"ACP_REMOTE_PASSWORD_{cleaned}"


@dataclass
class RemoteNode:
    """A single remote compute node reachable via SSH/SFTP.

    Attributes:
        name: Human-readable identifier (used in env-var lookup and logs).
        host: Hostname or IP address.
        port: SSH port (default 22).
        username: SSH login user.
        password: Plaintext password (prefer env-var override; see
            :meth:`resolved_password`). ``None`` means use key auth only.
        key_file: Path to a private key file (``~/.ssh/id_rsa`` etc.).
        remote_work_dir: Base directory for job working dirs on the remote
            node (e.g. ``/scratch/<user>/acp_jobs``).
        remote_code_dir: Directory where ACP source code is synced on the
            remote node (e.g. ``/home/<user>/acp_code``).
        python_executable: Interpreter used to run ``acp.cli`` on the node
            and to drive ``pip`` during :meth:`NodeManager.bootstrap_node`.
            Defaults to ``"python"``.  Set to ``"python3"``, an absolute
            path, or a venv interpreter (e.g.
            ``/opt/acp/venv/bin/python``) to pin a specific runtime per
            node — keeps node configuration portable across hosts whose
            default ``python`` differs.
        max_concurrent_jobs: Maximum simultaneous LSF jobs allowed on this
            node; also governs the SSH connection-pool size.
        enabled: Whether this node is eligible for job dispatch.
        host_key_policy: SSH host-key verification policy. One of
            ``"reject"`` (default — refuse unknown hosts, safest),
            ``"auto_add"`` (accept and record new hosts, for trusted
            internal networks), ``"warn"`` (log a warning but accept).
    """

    name: str
    host: str
    username: str
    remote_work_dir: str
    remote_code_dir: str
    port: int = 22
    password: str | None = None
    key_file: str | None = None
    python_executable: str = "python"
    max_concurrent_jobs: int = 5
    enabled: bool = True
    host_key_policy: str = "reject"

    def resolved_password(self) -> str | None:
        """Return the effective password, honouring env-var override.

        The environment variable ``ACP_REMOTE_PASSWORD_<NAME>`` (with ``-``
        and spaces turned to ``_``, upper-cased) takes precedence over the
        ``password`` field.  This keeps secrets out of YAML files.
        """
        env_val = os.environ.get(_env_var_name(self.name))
        return env_val or self.password

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #

    @classmethod
    def from_config_dict(cls, data: dict[str, Any]) -> RemoteNode:
        """Build a :class:`RemoteNode` from a YAML mapping.

        Required keys: ``name``, ``host``, ``username``,
        ``remote_work_dir``, ``remote_code_dir``.
        Optional keys: ``port``, ``password``, ``key_file``,
        ``max_concurrent_jobs``, ``enabled``.

        Raises:
            ValueError: If a required key is missing or ``remote_work_dir``
                contains a space (BSUB directives break on unquoted paths
                with spaces — see plan P2-6).
        """
        for required in ("name", "host", "username", "remote_work_dir", "remote_code_dir"):
            if required not in data:
                raise ValueError(f"RemoteNode config missing required key: {required!r}")

        remote_work_dir = str(data["remote_work_dir"])
        if " " in remote_work_dir:
            raise ValueError(
                f"RemoteNode {data['name']!r}: remote_work_dir must not contain spaces "
                f"(BSUB -o/-e directives break on unquoted paths): {remote_work_dir!r}"
            )

        return cls(
            name=str(data["name"]),
            host=str(data["host"]),
            username=str(data["username"]),
            remote_work_dir=remote_work_dir,
            remote_code_dir=str(data["remote_code_dir"]),
            port=int(data.get("port", 22)),
            password=data.get("password"),
            key_file=data.get("key_file"),
            python_executable=str(data.get("python_executable", "python")),
            max_concurrent_jobs=int(data.get("max_concurrent_jobs", 5)),
            enabled=bool(data.get("enabled", True)),
            host_key_policy=str(data.get("host_key_policy", "reject")),
        )


@dataclass
class RemoteExecutionConfig:
    """Top-level remote-execution policy.

    Attributes:
        execution_mode: ``'local'`` (default) or ``'remote'``.
        poll_interval: Seconds between remote status polls (default 15).
        retention_days: Days before remote job dirs are cleaned up.
        auto_sync: Whether to auto-sync code to nodes before submitting.
        nodes: List of configured :class:`RemoteNode` objects.
        max_concurrent_sessions: Maximum SFTP sessions in the connection pool.
        connect_timeout: Seconds to wait for an SSH connection.
        read_timeout: Seconds to wait for SFTP read operations.
    """

    execution_mode: str = "local"
    poll_interval: int = 15
    retention_days: int = 180
    auto_sync: bool = True
    queue: str = "normal"
    walltime: str = "24:00"
    extra_flags: str = ""
    nodes: list[RemoteNode] = field(default_factory=list)
    max_concurrent_sessions: int = 20
    connect_timeout: int = 10
    read_timeout: int = 30

    @property
    def is_remote(self) -> bool:
        """True when remote execution is active and at least one node exists."""
        return self.execution_mode == "remote" and bool(self.nodes)

    @property
    def walltime_seconds(self) -> int:
        """Parse :attr:`walltime` (``"HH:MM"`` or ``"HH:MM:SS"``) into seconds.

        Returns 0 if the value cannot be parsed.
        """
        return _parse_walltime(self.walltime)

    @property
    def enabled_nodes(self) -> list[RemoteNode]:
        """Subset of :attr:`nodes` with ``enabled=True``."""
        return [n for n in self.nodes if n.enabled]

    def get_node(self, name: str) -> RemoteNode | None:
        """Look up a node by name (case-sensitive)."""
        for node in self.nodes:
            if node.name == name:
                return node
        return None

    @classmethod
    def from_config_dict(cls, data: dict[str, Any]) -> RemoteExecutionConfig:
        """Build from the ``cluster`` section of a loaded YAML config.

        ``data`` is expected to be the ``cluster`` mapping.  Missing keys
        fall back to defaults, so a local-only config produces a no-op
        :class:`RemoteExecutionConfig`.

        Raises:
            ValueError: If ``execution_mode`` is not one of ``'local'`` or
                ``'remote'`` (catches typos like ``'remot'`` that would
                otherwise silently fall back to local — see plan P2-10).
        """
        execution_mode = str(data.get("execution_mode", "local"))
        if execution_mode not in ("local", "remote"):
            raise ValueError(
                f"Invalid execution_mode {execution_mode!r}; must be 'local' or 'remote'"
            )
        poll_interval = int(data.get("poll_interval", 15))
        retention_days = int(data.get("retention_days", 180))
        auto_sync = bool(data.get("auto_sync", True))
        queue = str(data.get("queue", "normal"))
        walltime = str(data.get("walltime", "24:00"))
        extra_flags = str(data.get("extra_flags", ""))
        max_concurrent_sessions = int(data.get("max_concurrent_sessions", 20))
        connect_timeout = int(data.get("connect_timeout", 10))
        read_timeout = int(data.get("read_timeout", 30))

        raw_nodes = data.get("nodes") or []
        nodes: list[RemoteNode] = []
        if isinstance(raw_nodes, list):
            for entry in raw_nodes:
                if isinstance(entry, dict):
                    nodes.append(RemoteNode.from_config_dict(entry))

        return cls(
            execution_mode=execution_mode,
            poll_interval=poll_interval,
            retention_days=retention_days,
            auto_sync=auto_sync,
            queue=queue,
            walltime=walltime,
            extra_flags=extra_flags,
            nodes=nodes,
            max_concurrent_sessions=max_concurrent_sessions,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )


def _parse_walltime(text: str) -> int:
    """Parse an LSF/BSUB wall-clock spec into whole seconds.

    Accepts ``"HH:MM"`` and ``"HH:MM:SS"``.  Returns 0 on parse failure.
    """
    if not text:
        return 0
    parts = text.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(nums) == 2:
        h, m = nums
        return h * 3600 + m * 60
    if len(nums) == 3:
        h, m, s = nums
        return h * 3600 + m * 60 + s
    return 0
