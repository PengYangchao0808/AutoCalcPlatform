"""Shared helper utilities for workflow modules.

Private helpers that are used by more than one workflow module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def sanitize_job_name(name: str) -> str:
    """Return a filesystem-safe job name."""
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_" for char in name.strip()
    )
    return cleaned.strip("._") or "job"


def write_result_summary(
    product_root: Path,
    workflow: str,
    products: list[dict[str, Any]],
) -> Path | None:
    """Write the Zone-C ``result_summary.json`` pointer file for a workflow.

    Zone-C contract: every workflow writes a small pointer file at its
    finalize step describing the finished products (label + relative path +
    kind). ``build_manifest(view="summary")`` discovers these files and
    surfaces the products as pinned entries in the file tree.

    Args:
        product_root: Directory that will contain the file (its products are
            referenced by paths relative to it).
        workflow: Workflow identifier (e.g. ``"energy"``).
        products: List of ``{"label", "path", "kind"}`` entries; ``path`` is
            relative to ``product_root``.

    Returns:
        The written file path, or ``None`` when no products were given.
    """
    if not products:
        return None
    summary_path = product_root / "result_summary.json"
    payload = {
        "version": 1,
        "workflow": workflow,
        "products": [
            {
                "label": str(item.get("label") or item.get("path") or ""),
                "path": str(item["path"]),
                "kind": str(item.get("kind") or "file"),
            }
            for item in products
            if isinstance(item, dict) and item.get("path")
        ],
    }
    if not payload["products"]:
        return None
    try:
        tmp = summary_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, summary_path)
        return summary_path
    except OSError:
        return None


__all__ = ["sanitize_job_name", "write_result_summary"]
