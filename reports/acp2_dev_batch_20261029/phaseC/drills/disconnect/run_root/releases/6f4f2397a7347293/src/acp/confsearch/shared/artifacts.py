"""Artifact helpers: hashing, atomic JSON, and bounded tree copies."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def file_sha256(path: Path | str) -> str:
    """Return the ``sha256:<hex>`` label for a file's contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_label(value: str | None) -> str | None:
    """Normalize a sha256 value to the ``sha256:<hex>`` form."""
    if not value:
        return None
    text = str(value).strip().lower()
    if text.startswith("sha256:"):
        return text
    return f"sha256:{text}"


def sha256_matches(path: Path, expected: str | None) -> bool:
    """True when *expected* is empty or matches the file's sha256."""
    if not expected:
        return True
    return file_sha256(path) == sha256_label(expected)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    """Atomically write a JSON document (tmp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def copy_tree_items(
    source_dir: Path,
    target_dir: Path,
    names: list[str] | None = None,
    max_bytes: int = 512 * 1024 * 1024,
) -> list[Path]:
    """Copy selected children of *source_dir* into *target_dir*.

    Args:
        source_dir: Directory whose direct children are copied.
        target_dir: Destination directory (created on demand).
        names: Restrict the copy to these child names; ``None`` copies all.
        max_bytes: Safety cap on total copied bytes (default 512 MiB).

    Returns:
        The list of copied destination paths.
    """
    if not source_dir.is_dir():
        return []
    import shutil

    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    total = 0
    children = sorted(source_dir.iterdir())
    for child in children:
        if names is not None and child.name not in names:
            continue
        try:
            if child.is_file():
                total += child.stat().st_size
            elif child.is_dir():
                for item in child.rglob("*"):
                    if item.is_file():
                        total += item.stat().st_size
        except OSError:
            continue
        if total > max_bytes:
            logger.warning(
                "Handoff copy capped at %d bytes — skipped remaining items after %s",
                max_bytes,
                child.name,
            )
            break
        dest = target_dir / child.name
        try:
            if child.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)
            copied.append(dest)
        except OSError as exc:
            logger.warning("Handoff copy failed for %s: %s", child, exc)
    return copied


__all__ = [
    "copy_tree_items",
    "file_sha256",
    "sha256_label",
    "sha256_matches",
    "write_json_atomic",
]
