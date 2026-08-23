"""Manifest loading for a task's ``RESULT/`` directory (design doc §8/§11)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from acp.storage.manifest import MANIFEST_FILENAME, Product, ProductKind, ResultManifest

logger = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_FILENAME",
    "Product",
    "ProductKind",
    "ResultManifest",
    "find_products",
    "load_result_manifest",
]


def load_result_manifest(task_dir: Path) -> ResultManifest | None:
    """Read ``<task_dir>/RESULT/result_manifest.json``; None when missing/corrupt."""
    result_dir = Path(task_dir) / "RESULT"
    try:
        return ResultManifest.read(result_dir)
    except FileNotFoundError:
        logger.debug("no %s under %s", MANIFEST_FILENAME, result_dir)
    except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        logger.debug("corrupt result manifest under %s: %s", result_dir, exc)
    return None


def find_products(manifest: ResultManifest, kind: str) -> list[Product]:
    """Filter manifest products by a :class:`ProductKind` value string (e.g. ``"structure"``)."""
    try:
        wanted = ProductKind(kind)
    except ValueError:
        logger.warning("unknown product kind %r; no products matched", kind)
        return []
    return [product for product in manifest.products if product.kind == wanted]
