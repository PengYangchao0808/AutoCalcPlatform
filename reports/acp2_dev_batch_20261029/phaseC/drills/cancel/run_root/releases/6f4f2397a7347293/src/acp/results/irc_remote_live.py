"""Bounded, on-demand mirroring of live IRC products from a remote node."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobRecord
from acp.scheduler.remote.fetcher import RemoteResultFetcher

_SNAPSHOT = "RESULT/trajectories/irc_trajectory.json"
_ORCA_DIR = "WORK/07_PATH/ORCA"
_MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_TRAJECTORY_BYTES = 64 * 1024 * 1024
_TRAJECTORY_NAME = re.compile(r"[A-Za-z0-9_.-]+_IRC_[FB]_trj\.xyz", re.IGNORECASE)


def _mirror_file(
    record: JobRecord,
    work_dir: Path,
    fetcher: RemoteResultFetcher,
    relative_path: str,
    *,
    size: int,
    mtime: float,
    limit: int,
    snapshot: bool = False,
) -> bool:
    """Copy a changed remote file atomically; return whether it is present."""
    if size <= 0 or size > limit:
        raise ValueError(f"IRC live file exceeds limit or is empty: {relative_path}")
    target = work_dir / relative_path
    if target.is_file() and not snapshot:
        local = target.stat()
        if local.st_size == size and abs(local.st_mtime - mtime) < 1:
            return True
    data = fetcher.read_file(record, relative_path)
    if len(data) > limit:
        raise ValueError(f"IRC live file exceeds limit: {relative_path}")
    if snapshot:
        payload: Any = json.loads(data)
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "irc_trajectory_v1"
            or not isinstance(payload.get("frames"), list)
        ):
            raise ValueError("Invalid remote IRC trajectory snapshot")
        if target.is_file() and target.read_bytes() == data:
            return True
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, target)
        os.utime(target, (mtime, mtime))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def refresh_remote_irc(
    record: JobRecord, work_dir: Path, fetcher: RemoteResultFetcher
) -> str:
    """Refresh a remote live snapshot or historical ORCA trajectories.

    Returns ``snapshot``, ``orca_trajectory``, or ``pending``. Callers can keep
    displaying the last successfully mirrored data if the remote read fails.
    """
    try:
        info = fetcher.file_stat(record, _SNAPSHOT)
    except FileNotFoundError:
        info = None
    if info is not None:
        _mirror_file(
            record, work_dir, fetcher, _SNAPSHOT,
            size=info.size, mtime=info.mtime, limit=_MAX_SNAPSHOT_BYTES, snapshot=True,
        )
        # The structure-viewer catalog reads the multi-frame path files. They
        # are optional while the writer is publishing a new snapshot.
        for direction in ("forward", "reverse"):
            relative = f"RESULT/irc/irc_{direction}_path.xyz"
            try:
                path_info = fetcher.file_stat(record, relative)
            except FileNotFoundError:
                continue
            _mirror_file(
                record, work_dir, fetcher, relative,
                size=path_info.size, mtime=path_info.mtime, limit=_MAX_TRAJECTORY_BYTES,
            )
        return "snapshot"

    # Older remote jobs may not publish the snapshot until ORCA exits. The
    # projection can already parse fully written frames from these raw files.
    try:
        entries = fetcher.list_files(record, relative_path=_ORCA_DIR)
    except FileNotFoundError:
        return "pending"
    copied = False
    for entry in entries:
        name = Path(entry.name).name
        if not _TRAJECTORY_NAME.fullmatch(name) or entry.is_dir:
            continue
        copied = _mirror_file(
            record, work_dir, fetcher, f"{_ORCA_DIR}/{name}",
            size=entry.size, mtime=entry.mtime, limit=_MAX_TRAJECTORY_BYTES,
        ) or copied
    return "orca_trajectory" if copied else "pending"


def ensure_remote_irc_point(
    record: JobRecord,
    work_dir: Path,
    fetcher: RemoteResultFetcher,
    direction: str,
    index: int,
) -> None:
    """Fetch one published point geometry for the IRC frame endpoint."""
    relative = f"RESULT/irc/irc_{direction}_point_{index:04d}.xyz"
    info = fetcher.file_stat(record, relative)
    _mirror_file(
        record, work_dir, fetcher, relative,
        size=info.size, mtime=info.mtime, limit=2 * 1024 * 1024,
    )


__all__ = ["ensure_remote_irc_point", "refresh_remote_irc"]
