"""
Remote Execution Configuration
==============================

Dataclasses describing remote compute nodes and the overall remote execution
policy. Parsed from the ``cluster`` section of the YAML configuration.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "DECLARED_SOFTWARE_NAMES",
    "NodeCapabilities",
    "RemoteNode",
    "RemoteExecutionConfig",
]

logger = logging.getLogger(__name__)

#: QC software names a node may declare under ``capabilities.software``.
#: MUST stay identical to the probe names in
#: ``acp.scheduler.remote.node_manager._DOCTOR_SOFTWARE_SCRIPT`` —
#: ``tests/test_node_capabilities.py::test_declared_enum_matches_doctor_probe_script``
#: locks the two sites against drift (plan node-selection-at-submission, T1).
DECLARED_SOFTWARE_NAMES: frozenset[str] = frozenset(
    {"orca", "xtb", "crest", "censo", "shermo", "isostat", "molclus"}
)

#: Cluster scheduler flavours a node may declare under ``type``.  Both
#: drive the identical bsub-compatible remote runner (design D2).
_VALID_NODE_TYPES: frozenset[str] = frozenset({"lsf", "openlava"})


@dataclass(frozen=True)
class NodeCapabilities:
    """Static capability declaration for a remote compute node.

    Consumed by the submission-time capability filter (design §1.1, D8/D13).
    A node without this declaration (``None`` on :class:`RemoteNode`) is
    treated as *generic*: software requirements fall back to probe-inferred
    or unknown state, and tag requirements can never be satisfied.

    Attributes:
        software: QC software names the node provides, drawn from
            :data:`DECLARED_SOFTWARE_NAMES` (deduplicated, order-preserving).
            Names outside the enum are dropped with a warning at parse time.
        tags: Free-form labels (e.g. ``"gpu"``) submissions may require;
            only declared tags ever satisfy a tag requirement (D13).
    """

    software: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


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
        bin_symlinks: Mapping ``{name: target}`` of symlinks created under
            ``~/bin`` on the node by :meth:`NodeManager.bootstrap_node`
            (e.g. ``{"Shermo": "/opt/shermo/Shermo"}``).  Declares the
            node's QC binaries in configuration instead of relying on the
            login shell PATH, which varies per cluster.
        max_concurrent_jobs: Maximum simultaneous LSF jobs allowed on this
            node; also governs the SSH connection-pool size.
        enabled: Whether this node is eligible for job dispatch.
        host_key_policy: SSH host-key verification policy. One of
            ``"reject"`` (default — refuse unknown hosts, safest),
            ``"auto_add"`` (accept and record new hosts, for trusted
            internal networks), ``"warn"`` (log a warning but accept).
        queue: Per-node LSF queue override (``#BSUB -q``). ``None`` (default)
            falls back to :attr:`RemoteExecutionConfig.queue` at submission
            time. Purely a submission attribute — never a filtering axis.
        capabilities: Static capability declaration
            (:class:`NodeCapabilities`). ``None`` (default) marks the node
            *generic* for the submission-time capability filter.
        type: Declarative cluster type — ``"lsf"`` (default) or
            ``"openlava"``. Purely informational today: both flavours drive
            the identical bsub-compatible remote runner; an unrecognised
            value falls back to ``"lsf"`` with a warning (never raises —
            see :func:`_parse_node_type`).
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
    bin_symlinks: dict[str, str] = field(default_factory=dict)
    max_concurrent_jobs: int = 5
    enabled: bool = True
    host_key_policy: str = "reject"
    queue: str | None = None
    capabilities: NodeCapabilities | None = None
    type: str = "lsf"

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
        ``max_concurrent_jobs``, ``enabled``, ``queue``, ``capabilities``,
        ``type``.
        A missing/blank ``queue`` or a missing ``capabilities`` block yields
        ``None`` (generic-node sentinel); unknown ``capabilities.software``
        names are dropped with a warning and never abort parsing; an
        unknown ``type`` falls back to ``"lsf"`` with a warning.

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

        node_name = str(data["name"])
        return cls(
            name=node_name,
            host=str(data["host"]),
            username=str(data["username"]),
            remote_work_dir=remote_work_dir,
            remote_code_dir=str(data["remote_code_dir"]),
            port=int(data.get("port", 22)),
            password=data.get("password"),
            key_file=data.get("key_file"),
            python_executable=str(data.get("python_executable", "python")),
            bin_symlinks=_parse_bin_symlinks(data.get("bin_symlinks")),
            max_concurrent_jobs=int(data.get("max_concurrent_jobs", 5)),
            enabled=bool(data.get("enabled", True)),
            host_key_policy=str(data.get("host_key_policy", "reject")),
            queue=_parse_node_queue(data.get("queue")),
            capabilities=_parse_capabilities(data.get("capabilities"), node_name),
            type=_parse_node_type(data.get("type"), node_name),
        )

    def to_config_dict(self) -> dict[str, Any]:
        """Serialize the node back to its YAML ``nodes:`` entry shape.

        Schema owner for whole-node persistence (init-wizard plan D17) —
        consumers must not hand-build node dicts.  Always emits ``name``,
        ``host``, ``username``, ``remote_work_dir``, ``remote_code_dir``,
        ``type`` and ``enabled``; every other field is emitted only when it
        differs from its parse default, so
        ``RemoteNode.from_config_dict(node.to_config_dict()) == node``
        holds and persisted configs stay minimal.
        """
        data: dict[str, Any] = {
            "name": self.name,
            "host": self.host,
            "username": self.username,
            "remote_work_dir": self.remote_work_dir,
            "remote_code_dir": self.remote_code_dir,
            "type": self.type,
            "enabled": self.enabled,
        }
        if self.port != 22:
            data["port"] = self.port
        if self.password is not None:
            data["password"] = self.password
        if self.key_file is not None:
            data["key_file"] = self.key_file
        if self.python_executable != "python":
            data["python_executable"] = self.python_executable
        if self.bin_symlinks:
            data["bin_symlinks"] = dict(self.bin_symlinks)
        if self.max_concurrent_jobs != 5:
            data["max_concurrent_jobs"] = self.max_concurrent_jobs
        if self.host_key_policy != "reject":
            data["host_key_policy"] = self.host_key_policy
        if self.queue is not None:
            data["queue"] = self.queue
        if self.capabilities is not None:
            data["capabilities"] = {
                "software": list(self.capabilities.software),
                "tags": list(self.capabilities.tags),
            }
        return data


@dataclass
class RemoteExecutionConfig:
    """Top-level remote-execution policy.

    Attributes:
        execution_mode: ``'local'`` (default) or ``'remote'``.
        poll_interval: Seconds between remote status polls (default 15).
        retention_days: Days before remote job dirs are cleaned up.
        auto_sync: Whether to auto-sync code to nodes before submitting.
        require_all_binaries: When True (default), pre-submit probes treat
            a missing workflow-required binary as a hard error that aborts
            the submission with configuration guidance.  Set False to keep
            the historical warn-only behaviour (e.g. binaries injected by
            the job scheduler environment only).
        pre_cmds: Shell lines injected into every generated LSF script
            before the ACP CLI runs (e.g. ``module load python/3.12``,
            ``source /opt/spack/share/spack/setup-env.sh``).  Makes module-
            based HPC clusters work without editing generated scripts.
        nodes: List of configured :class:`RemoteNode` objects.
        max_concurrent_sessions: Maximum SFTP sessions in the connection pool.
        connect_timeout: Seconds to wait for an SSH connection.
        read_timeout: Seconds to wait for SFTP read operations.
    """

    execution_mode: str = "local"
    poll_interval: int = 15
    retention_days: int = 180
    auto_sync: bool = True
    require_all_binaries: bool = True
    pre_cmds: list[str] = field(default_factory=list)
    queue: str = "normal"
    # Empty by default = no ``#BSUB -W`` walltime directive (jobs run to
    # completion).  Set ``cluster.walltime`` (e.g. "24:00") to re-enable a
    # hard LSF run-time limit.
    walltime: str = ""
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
        require_all_binaries = bool(data.get("require_all_binaries", True))
        raw_pre_cmds = data.get("pre_cmds") or []
        pre_cmds: list[str] = []
        if isinstance(raw_pre_cmds, list):
            pre_cmds = [str(c) for c in raw_pre_cmds if isinstance(c, str) and c.strip()]
        queue = str(data.get("queue", "normal"))
        walltime = str(data.get("walltime", ""))
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
            require_all_binaries=require_all_binaries,
            pre_cmds=pre_cmds,
            queue=queue,
            walltime=walltime,
            extra_flags=extra_flags,
            nodes=nodes,
            max_concurrent_sessions=max_concurrent_sessions,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )


def _parse_bin_symlinks(value: Any) -> dict[str, str]:
    """Parse the ``bin_symlinks`` mapping (``{name: target}``) from config.

    Non-mapping values (``None``, lists, scalars) produce an empty mapping
    so a typo never crashes node parsing.
    """
    if not isinstance(value, Mapping):
        return {}
    parsed: dict[str, str] = {}
    for name, target in value.items():
        if isinstance(target, str) and target.strip():
            parsed[str(name)] = target
    return parsed


def _parse_node_queue(value: Any) -> str | None:
    """Parse the optional per-node ``queue`` override.

    Missing or blank values yield ``None`` so the node falls back to
    :attr:`RemoteExecutionConfig.queue` at submission time.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_node_type(value: Any, node_name: str) -> str:
    """Parse the declarative cluster ``type`` (design D2 — never raises).

    Absent values default to ``"lsf"``.  Anything outside
    :data:`_VALID_NODE_TYPES` — including non-string values — logs a
    warning and falls back to ``"lsf"`` instead of aborting:
    :meth:`RemoteExecutionConfig.from_config_dict` runs at API-server
    startup and in ``acp doctor``, so one typo'd node entry must never
    take configuration loading down (mirrors the ``_parse_capabilities``
    never-abort precedent).
    """
    if value is None:
        return "lsf"
    if isinstance(value, str):
        text = value.strip()
        if text in _VALID_NODE_TYPES:
            return text
    logger.warning(
        "RemoteNode %r: unknown cluster type %r (valid: %s); falling back to 'lsf'",
        node_name,
        value,
        ", ".join(sorted(_VALID_NODE_TYPES)),
    )
    return "lsf"


def _parse_capabilities(value: Any, node_name: str) -> NodeCapabilities | None:
    """Parse the optional ``capabilities`` block into a :class:`NodeCapabilities`.

    A missing or non-mapping block yields ``None`` — the generic-node
    sentinel the downstream capability filter relies on.  Unknown software
    names are dropped with a warning; parsing never raises.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        logger.warning(
            "RemoteNode %r: capabilities block is not a mapping (%r); treating node as generic",
            node_name,
            value,
        )
        return None
    return NodeCapabilities(
        software=_parse_declared_software(value.get("software"), node_name),
        tags=_parse_name_tuple(value.get("tags"), node_name, "capabilities.tags"),
    )


def _parse_declared_software(value: Any, node_name: str) -> tuple[str, ...]:
    """Parse ``capabilities.software``: dedup, order-preserving, enum-checked.

    Names outside :data:`DECLARED_SOFTWARE_NAMES` are dropped with a warning
    listing the valid enum (design D3 — never abort config loading).
    """
    names: list[str] = []
    for item in _clean_string_list(value, node_name, "capabilities.software"):
        if item not in DECLARED_SOFTWARE_NAMES:
            logger.warning(
                "RemoteNode %r: dropping unknown declared software %r (valid names: %s)",
                node_name,
                item,
                ", ".join(sorted(DECLARED_SOFTWARE_NAMES)),
            )
            continue
        if item not in names:
            names.append(item)
    return tuple(names)


def _parse_name_tuple(value: Any, node_name: str, label: str) -> tuple[str, ...]:
    """Parse a free-form name list (``capabilities.tags``): dedup, order-preserving."""
    names: list[str] = []
    for item in _clean_string_list(value, node_name, label):
        if item not in names:
            names.append(item)
    return tuple(names)


def _clean_string_list(value: Any, node_name: str, label: str) -> list[str]:
    """Coerce a YAML list into stripped, non-empty strings.

    ``None`` yields an empty list; a non-list shape (scalar/typo) logs a
    warning and yields an empty list; non-string entries are dropped.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning("RemoteNode %r: %s is not a list (%r); ignoring", node_name, label, value)
        return []
    return [text for item in value if isinstance(item, str) and (text := item.strip())]


def _parse_walltime(text: str) -> int:
    """Parse an LSF/BSUB wall-clock spec into whole seconds.    Accepts ``"HH:MM"`` and ``"HH:MM:SS"``.  Returns 0 on parse failure.
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
