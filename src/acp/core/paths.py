"""Run-root resolution and filesystem-safety checks for ACP data storage.

Two-tier layout contract:

- Tier A: install directory (code, read-only) — no ACP data lives here.
- Tier B: data directory (``run_root``) — the SQLite index and per-project
  task trees (``WORK/`` + ``RESULT/``, including QC intermediate files)
  must live on a native filesystem; QC subprocess I/O on network/9p mounts
  is orders of magnitude slower.

Resolution priority: explicit CLI argument > ``ACP_RUN_ROOT`` env >
platform default. ``scripts/start_acp.sh`` and the generated systemd unit
pre-set ``ACP_RUN_ROOT=/var/lib/acp/runs`` for daemon deployments.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_ROOT_ENV_VAR = "ACP_RUN_ROOT"
ALLOW_SLOW_FS_ENV_VAR = "ACP_ALLOW_SLOW_FS"

_SLOW_FILESYSTEMS = frozenset(
    {"9p", "nfs", "nfs4", "cifs", "smbfs", "sshfs", "vfat", "exfat", "ntfs3", "fuse", "fuseblk"}
)
_MIN_FREE_BYTES = 1 << 30  # 1 GiB

__all__ = [
    "ALLOW_SLOW_FS_ENV_VAR",
    "RUN_ROOT_ENV_VAR",
    "check_run_root_safety",
    "mount_fstype_for",
    "platform_default_run_root",
    "resolve_run_root",
]


def platform_default_run_root() -> Path:
    """Return the platform-conventional data directory for ACP.

    Root deployments use ``/var/lib/acp/runs`` (matching the systemd unit);
    regular users use the XDG data home.  On Windows, uses
    ``%LOCALAPPDATA%/acp/runs`` or ``~/AppData/Local/acp/runs``.
    Falls back to the user directory when the system path is not writable.
    """
    # POSIX root-path branch (os.geteuid is unavailable on Windows).
    _geteuid = getattr(os, "geteuid", None)
    if _geteuid is not None and _geteuid() == 0:
        system_root = Path("/var/lib/acp/runs")
        probe = system_root
        while not probe.exists():
            probe = probe.parent
        if os.access(probe, os.W_OK):
            return system_root
        logger.warning("/var/lib/acp is not writable; falling back to the user data home")

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "acp" / "runs"
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "acp" / "runs"

    data_home = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(data_home).expanduser() / "acp" / "runs"


def resolve_run_root(explicit: str | Path | None = None) -> Path:
    """Resolve the data run root: CLI argument > env > platform default.

    Returns an absolute, user-expanded path. Does not create the directory —
    callers decide when to materialise it.
    """
    if explicit:
        root = Path(explicit)
    elif env_value := os.environ.get(RUN_ROOT_ENV_VAR):
        root = Path(env_value)
    else:
        root = platform_default_run_root()
    return Path(root).expanduser().resolve()


def mount_fstype_for(path: Path) -> str | None:
    """Return the filesystem type backing *path* on Linux (longest mount-prefix match).

    Returns ``None`` when the mount table is unavailable (non-Linux).
    """
    try:
        lines = Path("/proc/self/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    target = str(path)
    best: tuple[int, str] | None = None
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point = fields[1].replace("\\040", " ").replace("\\011", "\t")
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            length = len(mount_point)
            if best is None or length > best[0]:
                best = (length, fields[2])
    return best[1] if best else None


def _is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def check_run_root_safety(run_root: Path) -> list[str]:
    """Return human-readable warnings for unsafe run-root placement (empty = clean).

    Checks: slow/network filesystem (QC I/O penalty), overlap with the ACP
    install tree, and low free disk space. Warnings only — callers decide
    whether to proceed.
    """
    warnings: list[str] = []
    if not _is_truthy_env(ALLOW_SLOW_FS_ENV_VAR):
        fstype = mount_fstype_for(run_root)
        if fstype in _SLOW_FILESYSTEMS:
            warnings.append(
                f"run_root {run_root} is on a slow filesystem ({fstype!r}); "
                "QC subprocess I/O will be orders of magnitude slower. "
                f"Set {ALLOW_SLOW_FS_ENV_VAR}=1 to silence this warning."
            )
    install_root = Path(__file__).resolve().parents[3]
    if run_root == install_root or install_root in run_root.parents:
        warnings.append(
            f"run_root {run_root} lives inside the ACP install tree ({install_root}); "
            "keep data and code directories separate."
        )
    probe = run_root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    if free is not None and free < _MIN_FREE_BYTES:
        warnings.append(f"only {free / (1 << 30):.1f} GiB free on the volume backing {run_root}")
    return warnings
