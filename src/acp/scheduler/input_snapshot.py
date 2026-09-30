"""Helpers for durable, task-owned snapshots of submitted XYZ inputs."""

from __future__ import annotations

from typing import Any

__all__ = ["input_xyz_snapshot"]


def input_xyz_snapshot(inp: dict[str, Any]) -> str | None:
    """Return the submitted XYZ text for a job input, if it carries one.

    Supports direct ``xyz_text`` sources and ``batch_structures`` items
    (included items only); multi-item snapshots are joined with newlines.
    """
    source_type = str(inp.get("source_type") or "")
    if source_type == "xyz_text":
        source = inp.get("source")
        if isinstance(source, str) and source.strip():
            return source
        return None
    if source_type != "batch_structures":
        return None
    items = inp.get("items")
    if not isinstance(items, list):
        return None
    geometries = [
        item["xyz"].strip()
        for item in items
        if isinstance(item, dict)
        and item.get("include") is not False
        and isinstance(item.get("xyz"), str)
        and item["xyz"].strip()
    ]
    if geometries:
        return "\n".join(geometries) + "\n"
    return None
