"""PESsearch output persistence and canonical profile helpers.

New PESsearch jobs keep calculation intermediates under ``WORK/07_PATH``
and publish consumable products under ``RESULT/``.  This module is the
single writer for the PES profile and its result-manifest entries.

Recommendation isolation (2026-09-03): algorithmic TS/INT guesses are
audit-only.  They are stored in ``pes_recommendations.json`` and never
registered as ``kind: "structure"`` manifest products, so downstream
consumers (BatchOptimize, structure sources) only ever see manually
confirmed selections from ``pes_review.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from acp.storage.manifest import ProductKind, ResultManifest

PES_PROFILE_RELATIVE_PATH = "RESULT/pes_search/pes_profile.json"
PES_RECOMMENDATIONS_RELATIVE_PATH = "RESULT/pes_search/pes_recommendations.json"
PES_RECOMMENDATIONS_SCHEMA = "pes_recommendations_v1"
PES_SCAN_STAGE = "07_PATH"
PES_SCAN_DIR_NAME = "pes_scan_001"
PES_SCAN_RELATIVE_PATH = f"WORK/{PES_SCAN_STAGE}/{PES_SCAN_DIR_NAME}"

__all__ = [
    "PES_PROFILE_RELATIVE_PATH",
    "PES_RECOMMENDATIONS_RELATIVE_PATH",
    "PES_RECOMMENDATIONS_SCHEMA",
    "PES_SCAN_DIR_NAME",
    "PES_SCAN_RELATIVE_PATH",
    "PES_SCAN_STAGE",
    "copy_xyz_atomic",
    "persist_pes_outputs",
]


def copy_xyz_atomic(src: Path, dst: Path) -> None:
    """Atomically copy an XYZ file to *dst*."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(dst.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    try:
        handle.write(src.read_text(encoding="utf-8"))
        handle.close()
        os.replace(handle.name, dst)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def persist_pes_outputs(
    task_root: Path | str,
    *,
    scan_result: Mapping[str, Any],
    manifest_source: str | None = None,
    task_id: str = "",
    status: str = "completed",
) -> tuple[Path, Path]:
    """Write the canonical PES profile, recommendations audit, and manifest.

    Algorithmic recommendations are audit-only: they land in
    ``RESULT/pes_search/pes_recommendations.json`` and are registered as a
    ``report`` manifest product — never as ``structure`` products.  Only the
    manual review (``acp.calculations.pes.review``) adds structure products,
    so downstream consumers (BatchOptimize, structure sources) exclusively
    see manually confirmed selections.

    Returns:
        ``(pes_profile_path, result_manifest_path)``.
    """
    root = Path(task_root).expanduser().resolve()
    result_dir = root / "RESULT"
    pes_dir = result_dir / "pes_search"
    pes_dir.mkdir(parents=True, exist_ok=True)

    quality = dict(scan_result.get("quality") or {})
    frames = list(scan_result.get("frames") or [])
    ts_candidates = list(scan_result.get("ts_recommendations") or [])
    int_candidates = list(scan_result.get("int_recommendations") or [])
    scan_dir = str(scan_result.get("scan_dir_rel") or PES_SCAN_RELATIVE_PATH)
    payload: dict[str, Any] = {
        "schema_version": "pes_profile_v2",
        "workflow": "PESsearch",
        "mode": str(scan_result.get("mode") or "bond_length_scan"),
        "status": status,
        "coordinate": scan_result.get("coordinate") or {},
        "coordinates": list(scan_result.get("coordinates") or []),
        "selection": dict(scan_result.get("selection") or {}),
        "protocol": scan_result.get("protocol") or {},
        "scan_dir": scan_dir,
        "frames": frames,
        "profile": dict(scan_result.get("profile") or {}),
        "quality": quality,
        "ts_candidates": ts_candidates,
        "int_candidates": int_candidates,
        "recommendations": {"ts": ts_candidates, "intermediates": int_candidates},
        "frames_count": len(frames),
        "candidate_structures": {},
        "recommendations_file": "pes_search/pes_recommendations.json",
        "manifest_source": manifest_source,
    }
    profile_path = pes_dir / "pes_profile.json"
    _write_json_atomic(profile_path, payload)

    recommendations_payload: dict[str, Any] = {
        "schema_version": PES_RECOMMENDATIONS_SCHEMA,
        "workflow": "PESsearch",
        "note": "Algorithmic recommendations — audit only, never batch inputs; "
        "confirm selections via POST /jobs/{id}/pes/review.",
        "scan_dir": scan_dir,
        "ts": ts_candidates,
        "intermediates": int_candidates,
    }
    _write_json_atomic(pes_dir / "pes_recommendations.json", recommendations_payload)

    try:
        manifest = ResultManifest.read(result_dir)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        manifest = ResultManifest()
    manifest.task_id = task_id
    manifest.workflow = "PESsearch"
    manifest.status = status
    manifest.add_product(
        id="pes_profile",
        label="PESsearch energy profile",
        path="pes_search/pes_profile.json",
        kind=ProductKind.PES_PROFILE,
    )
    manifest.add_product(
        id="pes_recommendations",
        label="PESsearch algorithm recommendations (audit only)",
        path="pes_search/pes_recommendations.json",
        kind=ProductKind.REPORT,
    )
    manifest_path = manifest.write(result_dir)
    return profile_path, manifest_path


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically (temporary file followed by replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    try:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.close()
        os.replace(handle.name, path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
