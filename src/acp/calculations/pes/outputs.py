"""PESsearch output persistence and canonical profile helpers.

New PESsearch jobs keep calculation intermediates under ``WORK/07_PATH``
and publish consumable products under ``RESULT/``.  This module is the
single writer for the PES profile and its result-manifest entries.

Recommendation isolation (2026-09-03): algorithmic TS/INT guesses are
audit-only.  They are stored in ``pes_recommendations.json`` and never
registered as ``kind: "structure"`` manifest products, so downstream
consumers (BatchOptimize, structure sources) only ever see manually
confirmed selections from ``pes_review.json``.

Scan-frame products (2026-11-02, round-9 M2): every scan frame that really
exists on disk is exposed as an addressable ``structure`` product under
``RESULT/structures/frame_<NNN>.xyz`` with ``frame_index``/``candidate_id``
metadata.  The frames are **not** a batch input by themselves: their
``selection_source`` is ``"scan"``, so the manual-only gate (review
publication) still decides what BatchOptimize may consume.  PES also
publishes ``result_manifest.json`` (it always did), which is what
``from_artifact`` reads.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from acp.storage.manifest import Product, ProductKind, ResultManifest

logger = logging.getLogger(__name__)

PES_PROFILE_RELATIVE_PATH = "RESULT/pes_search/pes_profile.json"
PES_RECOMMENDATIONS_RELATIVE_PATH = "RESULT/pes_search/pes_recommendations.json"
PES_RECOMMENDATIONS_SCHEMA = "pes_recommendations_v1"
PES_SCAN_STAGE = "07_PATH"
PES_SCAN_DIR_NAME = "pes_scan_001"
PES_SCAN_RELATIVE_PATH = f"WORK/{PES_SCAN_STAGE}/{PES_SCAN_DIR_NAME}"

#: Hard bound on the number of scan-frame products one manifest may carry.
#: A frame product is one manifest row plus one hard-linked XYZ; 200 keeps the
#: manifest readable while still covering a dense curve.  When a scan exceeds
#: it, the two extremes and every candidate neighbourhood are always kept and
#: the rest is sampled evenly (see :func:`_select_published_frames`).
PES_MAX_PUBLISHED_FRAMES = 200
#: ``RESULT/``-relative directory of the published frame geometries.
PES_FRAME_STRUCTURES_SUBDIR = "structures"
#: Manifest metadata value distinguishing raw scan frames from published picks.
PES_FRAME_SELECTION_SOURCE = "scan"

__all__ = [
    "PES_FRAME_SELECTION_SOURCE",
    "PES_FRAME_STRUCTURES_SUBDIR",
    "PES_MAX_PUBLISHED_FRAMES",
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
    ts_candidates = []
    int_candidates = []
    scan_dir = str(scan_result.get("scan_dir_rel") or PES_SCAN_RELATIVE_PATH)
    frame_products, frame_publication = _publish_scan_frames(root, result_dir, scan_result)
    payload: dict[str, Any] = {
        "schema_version": "pes_profile_v2",
        "selection_mode": "manual_only",
        "workflow": "PESsearch",
        "mode": str(scan_result.get("mode") or "bond_length_scan"),
        "status": status,
        "coordinate": scan_result.get("coordinate") or {},
        "coordinates": list(scan_result.get("coordinates") or []),
        "selection": dict(scan_result.get("selection") or {}),
        "protocol": scan_result.get("protocol") or {},
        "optimization_level": scan_result.get("optimization_level") or {},
        "optimization_level_fingerprint": scan_result.get("optimization_level_fingerprint"),
        "level_warnings": list(scan_result.get("level_warnings") or []),
        "execution_mode": scan_result.get("execution_mode"),
        "scan_dir": scan_dir,
        "frames": frames,
        "profile": dict(scan_result.get("profile") or {}),
        "quality": quality,
        "ts_candidates": ts_candidates,
        "int_candidates": int_candidates,
        "recommendations": {"ts": ts_candidates, "intermediates": int_candidates},
        # Round-5 PR-08: the native recommendation algorithm stays disabled; the
        # empty arrays are policy, not a "no peak found" result.  Authorized
        # agents may still publish initial guesses through the PR-06 path.
        "recommendation_status": {
            "status": "disabled",
            "reason_code": "manual_only_policy",
            "note": (
                "native algorithmic recommendations are disabled; authorized "
                "agents publish selections via POST /jobs/{id}/pes/review "
                "(selector=agent) and manual confirmation remains available"
            ),
        },
        "frames_count": len(frames),
        # Round-9 M2: which frames became addressable products (manifest
        # metadata carries the same counters on the pes_profile product).
        "frame_products": dict(frame_publication),
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
        "status": "disabled",
        "reason_code": "manual_only_policy",
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
        metadata=frame_publication,
    )
    manifest.add_product(
        id="pes_recommendations",
        label="PESsearch algorithm recommendations (audit only)",
        path="pes_search/pes_recommendations.json",
        kind=ProductKind.REPORT,
    )
    for product in frame_products:
        manifest.add_product(
            id=product.id,
            label=product.label,
            path=product.path,
            kind=product.kind,
            metadata=product.metadata,
        )
    manifest_path = manifest.write(result_dir)
    return profile_path, manifest_path


# ── scan-frame products (round-9 M2) ────────────────────────────────────


def _frame_index(frame: Mapping[str, Any]) -> int | None:
    """Return a frame's integer index, or ``None`` when it has none."""
    value = frame.get("index")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _finite(value: Any) -> float | None:
    """Return *value* as a finite float, or ``None`` (never NaN/inf on disk)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _frame_energy(frame: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """Return the step-printed energy a frame really carries, and its source.

    The single-point value wins when the profile ran one; a failed/missing
    single point never falls back to the scan energy (round-8 V-10 separation
    is enforced by the profile writer, and this projection must not undo it).
    """
    single_point = _finite(frame.get("single_point_energy_hartree"))
    if single_point is not None:
        return single_point, "single_point"
    scan = _finite(frame.get("scan_energy_hartree"))
    if scan is not None:
        return scan, "scan"
    return None, None


def _scan_direction(coordinate: Mapping[str, Any]) -> str | None:
    """``increasing``/``decreasing`` when the coordinate's endpoints say so."""
    start = _finite(coordinate.get("start"))
    end = _finite(coordinate.get("end"))
    if start is None or end is None or start == end:
        return None
    return "increasing" if end > start else "decreasing"


def _candidate_frame_indices(scan_result: Mapping[str, Any]) -> set[int]:
    """Frame indices named by the (possibly disabled) recommendation rows."""
    indices: set[int] = set()
    for key in ("ts_recommendations", "int_recommendations"):
        rows = scan_result.get(key)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            value = row.get("frame_index")
            if isinstance(value, int) and not isinstance(value, bool):
                indices.add(value)
    return indices


def _select_published_frames(
    entries: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    limit: int,
    candidate_indices: set[int],
) -> list[tuple[int, Mapping[str, Any]]]:
    """Choose the frames to publish, preserving the profile's own order.

    Under the bound every frame is published.  Above it the two extremes and
    every candidate neighbourhood (frame +/-1) are mandatory; the remaining
    budget is filled with evenly spaced frames so the published subset still
    spans the curve.  Nothing is reordered by energy and no frame is invented.
    """
    total = len(entries)
    if total <= limit:
        return list(entries)
    available = {index for index, _frame in entries}
    chosen: list[int] = []
    seen: set[int] = set()

    def take(index: int) -> None:
        if index in available and index not in seen and len(chosen) < limit:
            seen.add(index)
            chosen.append(index)

    take(entries[0][0])
    take(entries[-1][0])
    for candidate in sorted(candidate_indices):
        for offset in (-1, 0, 1):
            take(candidate + offset)
    remaining = limit - len(chosen)
    if remaining > 0:
        step = (total - 1) / max(remaining - 1, 1)
        positions = (
            [0] if remaining == 1 else sorted({int(round(k * step)) for k in range(remaining)})
        )
        for position in positions:
            take(entries[min(max(position, 0), total - 1)][0])
        for index, _frame in entries:
            if len(chosen) >= limit:
                break
            take(index)
    return [(index, frame) for index, frame in entries if index in seen]


def _frame_source_path(
    root: Path,
    scan_result: Mapping[str, Any],
    frame: Mapping[str, Any],
    index: int,
) -> Path | None:
    """Resolve one frame's existing XYZ on disk (``None`` when it is gone)."""
    scan_dir_rel = str(scan_result.get("scan_dir_rel") or PES_SCAN_RELATIVE_PATH)
    bases: list[Path] = []
    raw_scan_dir = scan_result.get("scan_dir")
    if isinstance(raw_scan_dir, str) and raw_scan_dir.strip():
        base = Path(raw_scan_dir)
        bases.append(base if base.is_absolute() else root / base)
    bases.append(root / scan_dir_rel)

    candidates: list[Path] = []
    geometry = str(frame.get("geometry_path") or "")
    if geometry:
        candidate = Path(geometry)
        if candidate.is_absolute():
            candidates.append(candidate)
        else:
            for base in bases:
                candidates.append(base / geometry)
                candidates.append(base / "scan_frames" / candidate.name)
    for base in bases:
        candidates.append(base / "scan_frames" / f"frame_{index:03d}.xyz")
        candidates.append(base / f"frame_{index:03d}.xyz")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _publish_frame_geometry(source: Path, target: Path) -> bool:
    """Expose an existing scan frame under ``RESULT/structures/``.

    The published product is an addressable manifest path, but the bytes are
    not duplicated: a hard link shares the scan frame's inode, and only a
    filesystem that refuses links (cross-device) falls back to an atomic copy
    so the product stays readable.  Never rewrites an identical product and
    never invents a frame.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                if source.samefile(target):
                    return True
            except OSError:
                pass
            try:
                if target.is_file() and target.read_bytes() == source.read_bytes():
                    return True
            except OSError:
                pass
            target.unlink()
        try:
            os.link(source, target)
            return True
        except OSError:
            copy_xyz_atomic(source, target)
            return True
    except OSError:
        logger.warning("PES: could not publish scan frame %s", source, exc_info=True)
        return False


def _frame_product_metadata(
    frame: Mapping[str, Any],
    coordinate: Mapping[str, Any],
    *,
    index: int,
    direction: str | None,
) -> dict[str, Any]:
    """Manifest metadata of one published scan frame (never fabricated)."""
    metadata: dict[str, Any] = {
        "frame_index": index,
        # ``role`` keeps the manifest loader's explicit-role rule meaningful;
        # ``selection_source="scan"`` keeps raw frames out of the
        # manual-confirmation gate (they are audit evidence, not picks).
        "role": "scan_frame",
        "selection_source": PES_FRAME_SELECTION_SOURCE,
        # The stable identity an agent uses when publishing a selection.
        "candidate_id": f"frame_{index:03d}",
        "scan_coordinate": {
            "kind": coordinate.get("kind"),
            "atoms": list(coordinate.get("atoms") or []),
            "unit": frame.get("coordinate_unit") or coordinate.get("unit"),
            "target": _finite(frame.get("target_coordinate")),
            "actual": _finite(frame.get("actual_coordinate")),
        },
    }
    if direction is not None:
        metadata["direction"] = direction
    for key in (
        "target_coordinates",
        "actual_coordinates",
        "constraint_residuals",
    ):
        values = frame.get(key)
        if isinstance(values, Mapping) and values:
            metadata[key] = dict(values)
    residual = _finite(frame.get("max_constraint_residual"))
    if residual is not None:
        metadata["residual"] = residual
    accepted = frame.get("constraint_residual_ok")
    if isinstance(accepted, bool):
        metadata["accepted"] = accepted
    frame_role = frame.get("frame_role")
    if isinstance(frame_role, str) and frame_role:
        metadata["frame_role"] = frame_role
    converged = frame.get("optimization_converged")
    if isinstance(converged, bool):
        metadata["optimization_converged"] = converged
    energy, energy_source = _frame_energy(frame)
    if energy is not None:
        metadata["energy_hartree"] = energy
        metadata["energy_source"] = energy_source
        # The curve point this frame contributes (same value, explicit name).
        metadata["profile_point"] = energy
    return metadata


def _publish_scan_frames(
    root: Path,
    result_dir: Path,
    scan_result: Mapping[str, Any],
) -> tuple[list[Product], dict[str, Any]]:
    """Materialise addressable frame products; returns ``(products, counters)``.

    Frames whose geometry is missing on disk are skipped (never invented);
    their absence is reported in the counters so a reader can tell a truncated
    publication from a complete one.
    """
    frames = list(scan_result.get("frames") or [])
    entries = [
        (index, frame)
        for frame in frames
        if isinstance(frame, Mapping) and (index := _frame_index(frame)) is not None
    ]
    selected = _select_published_frames(
        entries,
        limit=PES_MAX_PUBLISHED_FRAMES,
        candidate_indices=_candidate_frame_indices(scan_result),
    )
    coordinate = dict(scan_result.get("coordinate") or {})
    direction = _scan_direction(coordinate)
    products: list[Product] = []
    for index, frame in selected:
        source = _frame_source_path(root, scan_result, frame, index)
        if source is None:
            logger.debug("PES: scan frame %s has no geometry on disk; not published", index)
            continue
        relative = f"{PES_FRAME_STRUCTURES_SUBDIR}/frame_{index:03d}.xyz"
        if not _publish_frame_geometry(source, result_dir / relative):
            continue
        products.append(
            Product(
                id=f"frame_{index:03d}",
                label=f"PES scan frame {index}",
                path=relative,
                kind=ProductKind.STRUCTURE,
                metadata=_json_safe(
                    _frame_product_metadata(frame, coordinate, index=index, direction=direction)
                ),
            )
        )
    counters: dict[str, Any] = {
        "frames_total": len(frames),
        "frames_selected": len(selected),
        "frames_published": len(products),
        "frames_missing": len(selected) - len(products),
        "truncated": len(selected) < len(entries),
        "max_published_frames": PES_MAX_PUBLISHED_FRAMES,
    }
    return products, counters


def _json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats (NaN/±Inf) with ``None``.

    Scan frames carry ``float("nan")`` when coordinate measurement fails
    (``scan.py::_extract_frames``).  Persisting those via ``json.dump``
    writes bare ``NaN`` tokens that violate strict JSON and later crash
    the API encoder (``Out of range float values are not JSON
    compliant``).  ``None`` is the canonical "unmeasured" marker on disk.
    """
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


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
        json.dump(
            _json_safe(payload), handle, indent=2, sort_keys=True, default=str, allow_nan=False
        )
        handle.close()
        os.replace(handle.name, path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
