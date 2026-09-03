"""PESsearch output persistence and canonical profile helpers.

New PESsearch jobs keep calculation intermediates under ``WORK/07_PATH``
and publish consumable products under ``RESULT/``.  This module is the
single writer for the PES profile and its result-manifest entries.
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
PES_SCAN_STAGE = "07_PATH"
PES_SCAN_DIR_NAME = "pes_scan_001"
PES_SCAN_RELATIVE_PATH = f"WORK/{PES_SCAN_STAGE}/{PES_SCAN_DIR_NAME}"

__all__ = [
    "PES_PROFILE_RELATIVE_PATH",
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
    candidate_structures: Mapping[str, Path],
    manifest_source: str | None = None,
    task_id: str = "",
    status: str = "completed",
) -> tuple[Path, Path]:
    """Write the canonical PES profile and ``RESULT/result_manifest.json``.

    ``candidate_structures`` contains already-materialised files under
    ``RESULT/structures``.  All paths persisted in the profile and result
    manifest are relative to the task root or ``RESULT/`` respectively.

    Returns:
        ``(pes_profile_path, result_manifest_path)``.
    """
    root = Path(task_root).expanduser().resolve()
    result_dir = root / "RESULT"
    pes_dir = result_dir / "pes_search"
    structures_dir = result_dir / "structures"
    pes_dir.mkdir(parents=True, exist_ok=True)
    structures_dir.mkdir(parents=True, exist_ok=True)

    candidate_paths: dict[str, str] = {}
    for candidate_id, path in candidate_structures.items():
        candidate_path = Path(path).resolve()
        try:
            candidate_paths[str(candidate_id)] = candidate_path.relative_to(result_dir).as_posix()
        except ValueError:
            raise ValueError(
                f"PES candidate path must be under RESULT/: {candidate_path}"
            ) from None

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
        "candidate_structures": candidate_paths,
        "manifest_source": manifest_source,
    }
    profile_path = pes_dir / "pes_profile.json"
    _write_json_atomic(profile_path, payload)

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
    for candidate_id, result_relative_path in candidate_paths.items():
        kind = next(
            (
                "TS" if str(candidate.get("kind") or "").lower() == "ts" else "INT"
                for candidate in ts_candidates + int_candidates
                if str(candidate.get("candidate_id") or "") == candidate_id
            ),
            "candidate",
        )
        manifest.add_product(
            id=f"pes_candidate_{candidate_id}",
            label=f"PESsearch {kind} candidate {candidate_id}",
            path=result_relative_path,
            kind=ProductKind.STRUCTURE,
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
