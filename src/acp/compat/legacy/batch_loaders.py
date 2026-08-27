"""Legacy manifest readers adapted to generic batch items."""
# pyright: basic, reportArgumentType=false, reportReturnType=false, reportUnusedCallResult=false

from __future__ import annotations

from pathlib import Path

from acp.calculations.batch._items import BatchStructureItem, JsonObject
from acp.calculations.batch._tag import build_tag_title, normalize_tag
from acp.calculations.batch._xyz import (
    _assign_ids,
    _items_from_text,
    _mapping,
    _rewrite_comment,
    _rows,
    _text,
)
from acp.calculations.contracts import StructureRole
from acp.compat.legacy.manifests import (
    read_s2_candidate_manifest,
    read_s2_path_manifest,
    read_s3_lowconfirm_manifest,
)


def _resolve_path(manifest_path: Path, reference: str, scan_dir: str = "") -> Path | None:
    if not reference:
        return None
    root = manifest_path.parent.parent.parent
    probes = (
        manifest_path.parent / reference,
        manifest_path.parent.parent / reference,
        root / reference,
        root / scan_dir / reference if scan_dir else manifest_path.parent / reference,
    )
    return next((probe.resolve() for probe in probes if probe.is_file()), None)


def _frame_reference(payload: JsonObject, frame_index: int | None) -> str:
    scan = _mapping(payload.get("scan")) or {}
    for frame in _rows(scan.get("frames")):
        raw_index = frame.get("frame_index")
        if isinstance(raw_index, (int, float, str)) and int(raw_index) == frame_index:
            return _text(frame.get("path") or frame.get("xyz") or frame.get("geometry"))
    return ""


def _row_id(row: JsonObject) -> str:
    return _text(row.get("candidate_id") or row.get("id"))


def _row_tag(row: JsonObject) -> str:
    for key in ("role", "kind", "tag", "recommended_role"):
        value = row.get(key)
        tag = normalize_tag(value if isinstance(value, str) else None)
        if tag:
            return tag
    return "INT"


def _items_from_geometry(
    geometry: Path,
    *,
    tag: str,
    candidate_id: str,
    source_ref: str,
    name: str,
    frame_index: int | None,
) -> list[BatchStructureItem]:
    items = _items_from_text(
        geometry.read_text(encoding="utf-8"), "legacy_manifest", source_ref, name
    )
    for item in items:
        item.tag = tag
        item.role = StructureRole.TRANSITION_STATE if tag == "TS" else StructureRole.MINIMUM
        item.candidate_id = candidate_id or item.candidate_id
        item.xyz = _rewrite_comment(
            item.xyz,
            build_tag_title(
                tag,
                candidate_id=item.candidate_id,
                source="artifact",
                frame=frame_index,
            ),
        )
    return items


def _load_rows(
    manifest_path: Path,
    payload: JsonObject,
    rows: list[JsonObject],
    select: list[str] | None,
) -> list[BatchStructureItem]:
    ids = {_row_id(row) for row in rows}
    if select:
        missing = [candidate_id for candidate_id in select if candidate_id not in ids]
        if missing:
            raise ValueError(f"Unknown candidate ids in manifest: {', '.join(missing)}")
        rows = [row for candidate_id in select for row in rows if _row_id(row) == candidate_id]
    scan = _mapping(payload.get("scan")) or {}
    scan_dir = _text(scan.get("scan_dir"))
    items: list[BatchStructureItem] = []
    for row in rows:
        candidate_id = _row_id(row)
        reference = _text(
            row.get("geometry")
            or row.get("geometry_path")
            or row.get("xyz")
            or row.get("optimized_xyz")
            or row.get("path")
        )
        raw_frame = row.get("frame_index")
        frame_index = int(raw_frame) if isinstance(raw_frame, (int, float, str)) else None
        geometry = _resolve_path(
            manifest_path,
            reference or _frame_reference(payload, frame_index),
            scan_dir,
        )
        if geometry is None:
            raise FileNotFoundError(
                f"Structure geometry missing for {candidate_id or '<unnamed>'}: {reference}"
            )
        items.extend(
            _items_from_geometry(
                geometry,
                tag=_row_tag(row),
                candidate_id=candidate_id,
                source_ref=f"{manifest_path}#{candidate_id}",
                name=_text(row.get("name"), candidate_id),
                frame_index=frame_index,
            )
        )
    return _assign_ids(items)


def load_items_from_s2_candidate_manifest(
    manifest_path: Path | str,
    candidate_ids: list[str] | None = None,
) -> list[BatchStructureItem]:
    """Load active rows from a read-only candidate manifest."""
    path = Path(manifest_path)
    payload = read_s2_candidate_manifest(path)
    if payload is None:
        raise ValueError(f"No candidate manifest at or next to {path}")
    rows = _rows(payload.get("candidates"))
    if candidate_ids:
        rows = [row for row in rows if _row_id(row) in set(candidate_ids)]
    else:
        rows = [row for row in rows if row.get("active", True) is not False]
    return _load_rows(
        path
        if path.name == "s2_candidate_manifest.json"
        else path.with_name("s2_candidate_manifest.json"),
        payload,
        rows,
        candidate_ids,
    )


def load_items_from_s2_path_manifest(
    manifest_path: Path | str,
    select: list[str] | None = None,
) -> tuple[list[BatchStructureItem], JsonObject]:
    """Load legacy path candidates through the compat reader."""
    path = Path(manifest_path)
    payload = read_s2_path_manifest(path)
    candidate_payload = read_s2_candidate_manifest(path)
    if candidate_payload is not None:
        candidate_path = path.with_name("s2_candidate_manifest.json")
        rows = _rows(candidate_payload.get("candidates"))
        rows = [row for row in rows if row.get("active", True) is not False]
        return _load_rows(candidate_path, payload, rows, select), payload
    rows = _rows(payload.get("candidates"))
    recommendations = _mapping(payload.get("recommendations")) or {}
    if not rows:
        for key in ("ts", "intermediates"):
            for row in _rows(recommendations.get(key)):
                row.setdefault("id", row.get("candidate_id"))
                row.setdefault("kind", "ts" if key == "ts" else "minimum")
                rows.append(row)
    if not rows:
        raise ValueError("Manifest carries no structure candidates")
    if not select:
        ts_rows = [row for row in rows if _row_tag(row) == "TS"]
        rows = ts_rows or rows
    return _load_rows(path, payload, rows, select), payload


def load_items_from_s3_manifest(
    manifest_path: Path | str,
    select: list[str] | None = None,
) -> tuple[list[BatchStructureItem], JsonObject]:
    """Load confirmed legacy rows through the compat reader."""
    path = Path(manifest_path)
    payload = read_s3_lowconfirm_manifest(path)
    rows = [
        row for row in _rows(payload.get("candidates")) if _text(row.get("status")) == "confirmed"
    ]
    if not rows:
        raise ValueError("Manifest has no confirmed structure candidates")
    return _load_rows(path, payload, rows, select), payload


__all__ = [
    "load_items_from_s2_candidate_manifest",
    "load_items_from_s2_path_manifest",
    "load_items_from_s3_manifest",
]
