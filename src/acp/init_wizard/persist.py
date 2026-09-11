"""Merge-safe YAML persistence engine for the ACP init wizard (D1/D5/D7).

Read-modify-writes ONLY the raw target config file — never the merged
6-source view (root AGENTS.md anti-pattern #3). Writes are atomic
(tmp + ``os.replace``), preceded by a timestamped ``.bak`` backup, and
guarded by a writability pre-flight so a read-only target aborts before
anything is touched.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

__all__ = [
    "InitAbort",
    "load_target",
    "save_target",
    "set_cluster_type",
    "set_execution_mode_remote",
    "set_executable_path",
    "upsert_node",
]


class InitAbort(Exception):  # noqa: N818 — name pinned by the acp-init plan (D1/T3)
    """User-facing fatal abort: wizard stops and reports the carried reason."""


def load_target(path: Path) -> dict[str, Any]:
    """Load the raw target YAML file.

    A missing file or an empty file (``yaml.safe_load`` returning None)
    loads as ``{}`` — the ``cccp.config`` empty-file precedent. A parse
    error or a non-dict, non-None root aborts: a user config file is never
    auto-rewritten.

    Args:
        path: Target YAML file path.

    Returns:
        The raw mapping stored in the file (``{}`` when absent/empty).

    Raises:
        InitAbort: On unreadable file, YAML parse error, or non-dict root.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InitAbort(f"无法读取配置文件：{path}（errno {exc.errno}: {exc.strerror}）") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InitAbort(f"配置文件解析失败：{path}（{exc}）") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise InitAbort(f"配置文件根节点必须是映射，{path} 实际为 {type(data).__name__}")
    return data


def _backup_timestamp() -> str:
    """Timestamp used in backup file names (monkeypatch-friendly seam)."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup_existing(path: Path) -> Path:
    """Copy *path* to ``<name>.bak-<ts>`` (``-N`` on same-second collision).

    Args:
        path: Existing target file to back up.

    Returns:
        The backup file path actually used.
    """
    stamp = _backup_timestamp()
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 0
    while backup.exists():
        counter += 1
        backup = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
    shutil.copy2(path, backup)
    logger.info("已备份 %s -> %s", path, backup)
    return backup


def save_target(path: Path, data: dict[str, Any], mode: int | None = None) -> None:
    """Atomically write *data* to *path*, backing up any prior content.

    The writability pre-flight runs first: an existing target must pass
    ``os.access(path, os.W_OK)``; a missing target requires a writable
    parent directory. Failure aborts before any file is touched —
    ``os.replace`` itself only needs directory write permission, so the
    pre-flight is what keeps a read-only target from being silently
    replaced. The effective file mode is the explicit *mode*, else the
    existing target's ``st_mode & 0o777``, else ``0o644`` — a restrictive
    existing file is never widened. The tmp file is chmod'ed to the
    effective mode before ``os.replace`` so the final path never appears
    world-readable.

    Args:
        path: Target YAML file path.
        data: Raw mapping to persist.
        mode: Explicit permission bits for the written file.

    Raises:
        InitAbort: On failed pre-flight, or any OSError during
            backup/tmp-write/chmod/replace (tmp file is cleaned up).
    """
    if path.exists():
        if not os.access(path, os.W_OK):
            raise InitAbort(f"目标文件不可写：{path}（权限拒绝）")
        effective_mode = mode if mode is not None else path.stat().st_mode & 0o777
    else:
        parent = path.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK):
            raise InitAbort(f"目标目录不可写或不存在：{parent}")
        effective_mode = 0o644 if mode is None else mode

    text = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        if path.exists():
            _backup_existing(path)
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_path, effective_mode)
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("临时文件清理失败：%s", tmp_path)
        raise InitAbort(f"写入配置文件失败：{path}（errno {exc.errno}: {exc.strerror}）") from exc
    logger.info("已写入配置 %s（mode=%04o）", path, effective_mode)


def set_executable_path(data: dict[str, Any], name: str, path: Path) -> None:
    """Set ``data["executables"][name]["path"]``, preserving sibling keys.

    Args:
        data: Raw target mapping (mutated in place).
        name: Executable name (e.g. ``xtb``).
        path: Executable path to persist.
    """
    executables = data.setdefault("executables", {})
    entry = executables.setdefault(name, {})
    if not isinstance(entry, dict):
        logger.warning("executables.%s 不是映射，已替换（原值 %r）", name, entry)
        entry = {}
        executables[name] = entry
    entry["path"] = str(path)


def set_cluster_type(data: dict[str, Any], node_type: str) -> None:
    """Set ``data["cluster"]["type"]`` only when absent or ``"local"``.

    A differing existing non-local value is left untouched — the caller
    owns the overwrite confirmation (D2).

    Args:
        data: Raw target mapping (mutated in place).
        node_type: Cluster type to declare (``lsf`` or ``openlava``).
    """
    cluster = data.setdefault("cluster", {})
    current = cluster.get("type")
    if current is None or current == "local":
        cluster["type"] = node_type
    else:
        logger.info("cluster.type 已为 %r，保持不变（覆盖由调用方确认）", current)


def set_execution_mode_remote(data: dict[str, Any]) -> None:
    """Set ``data["cluster"]["execution_mode"] = "remote"`` (D3).

    Args:
        data: Raw target mapping (mutated in place).
    """
    data.setdefault("cluster", {})["execution_mode"] = "remote"


def upsert_node(data: dict[str, Any], node_dict: dict[str, Any]) -> None:
    """Insert or replace a node entry in ``data["cluster"]["nodes"]`` by name.

    Collision confirmation is the caller's job (D14d) — this function
    always applies replace-by-name semantics.

    Args:
        data: Raw target mapping (mutated in place).
        node_dict: Node mapping; its ``name`` identifies the entry.
    """
    cluster = data.setdefault("cluster", {})
    nodes = cluster.setdefault("nodes", [])
    name = node_dict.get("name")
    for index, existing in enumerate(nodes):
        if isinstance(existing, dict) and existing.get("name") == name:
            nodes[index] = node_dict
            return
    nodes.append(node_dict)
