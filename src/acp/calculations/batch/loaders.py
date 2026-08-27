"""Result-manifest and batch-request loaders."""
# pyright: basic, reportArgumentType=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnusedCallResult=false

from __future__ import annotations

import json
import re
from pathlib import Path

from acp.calculations.contracts import StructureRole
from acp.results.manifest import find_products, load_result_manifest

from ._items import BatchStructureItem, JsonObject, JsonValue, apply_user_overrides
from ._tag import build_tag_title, normalize_tag, parse_tag_comment
from ._xyz import (
    _assign_ids,
    _first_comment,
    _items_from_text,
    _rewrite_comment,
    load_items_from_xyz_file,
    load_items_from_xyz_text,
)

BATCH_REQUEST_SCHEMA_VERSION = "batch_structures_v1"
_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:ts|int)_(?:guess|candidate)_[A-Za-z0-9_-]+)", re.IGNORECASE
)
_S2_ID_RE = re.compile(r"s2[_-]candidate[_-]([A-Za-z0-9_.-]+)", re.IGNORECASE)


def _text(value: JsonValue | None, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _rows(value: JsonValue | None) -> list[JsonObject]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _product_metadata(task_dir: Path) -> dict[str, JsonObject]:
    path = task_dir / "RESULT" / "result_manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {_text(row.get("id")): row for row in _rows(raw.get("products")) if _text(row.get("id"))}


def _product_candidate(product_id: str, label: str, path: str, metadata: JsonObject) -> str:
    explicit = _text(metadata.get("candidate_id"))
    if explicit:
        return explicit
    for value in (product_id, label, path):
        match = _CANDIDATE_RE.search(value)
        if match:
            return match.group(1)
        match = _S2_ID_RE.search(value)
        if match:
            return match.group(1).removesuffix(".xyz")
    return Path(path).stem or product_id


def _product_tag(product_id: str, label: str, path: str, metadata: JsonObject, comment: str) -> str:
    for key in ("role", "tag"):
        value = metadata.get(key)
        tag = normalize_tag(value if isinstance(value, str) else None)
        if tag:
            return tag
    parsed = parse_tag_comment(comment)["tag"]
    if parsed:
        return parsed
    return (
        "TS" if re.search(r"\bTS\b", " ".join((product_id, label, path)), re.IGNORECASE) else "INT"
    )


def load_items_from_result_manifest(task_dir: Path | str) -> list[BatchStructureItem]:
    """Load structure products from a task's unified result manifest."""
    root = Path(task_dir)
    manifest = load_result_manifest(root)
    if manifest is None:
        return []
    products = find_products(manifest, "structure")
    if not products:
        import logging

        logging.getLogger(__name__).warning(
            "result manifest contains no structure products: %s", root / "RESULT"
        )
        return []
    metadata_by_id = _product_metadata(root)
    items: list[BatchStructureItem] = []
    for product in products:
        geometry = root / "RESULT" / product.path
        if not geometry.is_file():
            continue
        metadata = metadata_by_id.get(product.id, {})
        text = geometry.read_text(encoding="utf-8")
        tag = _product_tag(product.id, product.label, product.path, metadata, _first_comment(text))
        candidate_id = _product_candidate(product.id, product.label, product.path, metadata)
        product_items = _items_from_text(
            text, "result_manifest", str(geometry), product.label or product.id
        )
        for item in product_items:
            item.tag = tag
            item.role = StructureRole.TRANSITION_STATE if tag == "TS" else StructureRole.MINIMUM
            item.candidate_id = candidate_id
            item.xyz = _rewrite_comment(
                item.xyz, build_tag_title(tag, candidate_id=candidate_id, source="result_manifest")
            )
        items.extend(product_items)
    return _assign_ids(items)


def _task_dir_for_artifact(path: Path) -> Path:
    if path.name == "result_manifest.json" and path.parent.name == "RESULT":
        return path.parent.parent
    if path.is_dir() and (path / "RESULT" / "result_manifest.json").is_file():
        return path
    return path.parent.parent if path.parent.name == "RESULT" else path.parent


def _request_geometry(entry: JsonObject, base_dir: Path) -> list[BatchStructureItem]:
    reference = _text(entry.get("geometry") or entry.get("path"))
    if not reference:
        raise ValueError(f"Batch entry has no geometry path: {entry}")
    path = Path(reference)
    if not path.is_absolute():
        path = base_dir / path
    items = load_items_from_xyz_file(
        path, source_type=_text(entry.get("source_type"), "batch_request")
    )
    requested_id = _text(entry.get("id") or entry.get("item_id"))
    candidate = _text(entry.get("candidate_id"), requested_id)
    tag = normalize_tag(entry.get("role") if isinstance(entry.get("role"), str) else None)
    tag = tag or normalize_tag(entry.get("tag") if isinstance(entry.get("tag"), str) else None)
    for index, item in enumerate(items, 1):
        item.item_id = requested_id if len(items) == 1 and requested_id else item.item_id
        if requested_id and len(items) > 1:
            item.item_id = f"{requested_id}__frame_{index:03d}"
        item.candidate_id = candidate or item.candidate_id
        item.name = _text(entry.get("name"), requested_id or item.name)
        if tag:
            item.tag = tag
            item.role = StructureRole.TRANSITION_STATE if tag == "TS" else StructureRole.MINIMUM
            item.xyz = _rewrite_comment(
                item.xyz, build_tag_title(tag, candidate_id=item.candidate_id, source="user")
            )
        if entry.get("include") is False:
            item.include = False
    return items


def load_batch_request(payload: JsonObject | Path | str) -> list[BatchStructureItem]:
    """Expand a ``batch_structures_v1`` request into structure items."""
    base_dir = Path.cwd()
    if isinstance(payload, (Path, str)):
        request_path = Path(payload)
        base_dir = request_path.parent.resolve()
        raw = json.loads(request_path.read_text(encoding="utf-8"))
    else:
        raw = payload
    if not isinstance(raw, dict):
        raise ValueError("Batch request must be a JSON object")
    version = raw.get("schema_version")
    if version is not None and version != BATCH_REQUEST_SCHEMA_VERSION:
        raise ValueError(f"Unsupported batch request schema: {version!r}")
    if "irc" in raw:
        raise ValueError("BatchOptimize requests do not accept IRC")
    entries: list[JsonObject] = []
    for key in ("items", "structures", "manifests", "files"):
        entries.extend(_rows(raw.get(key)))
    if not entries:
        raise ValueError("Batch request carries no structure entries")
    items: list[BatchStructureItem] = []
    for entry in entries:
        source_type = _text(entry.get("source_type"))
        artifact = entry.get("from_artifact") or entry.get("manifest")
        if isinstance(artifact, str) and (
            source_type in {"result", "result_manifest"} or "result_manifest" in artifact
        ):
            items.extend(
                load_items_from_result_manifest(
                    _task_dir_for_artifact((base_dir / artifact).resolve())
                )
            )
        elif isinstance(artifact, str):
            raise ValueError(
                "BatchOptimize requests accept result_manifest artifacts; "
                "stage manifests require the compatibility boundary"
            )
        elif entry.get("geometry") or entry.get("path"):
            items.extend(_request_geometry(entry, base_dir))
        elif entry.get("xyz"):
            entry_items = load_items_from_xyz_text(
                _text(entry.get("xyz")),
                source_type=source_type or "upload",
                source_ref=_text(entry.get("source_ref"), _text(entry.get("name"), "mol")),
                base_name=_text(entry.get("name"), "mol"),
            )
            tag = normalize_tag(entry.get("role") if isinstance(entry.get("role"), str) else None)
            if tag:
                for item in entry_items:
                    item.tag = tag
                    item.role = (
                        StructureRole.TRANSITION_STATE if tag == "TS" else StructureRole.MINIMUM
                    )
            items.extend(entry_items)
        else:
            raise ValueError(f"Batch entry has neither xyz, geometry nor manifest: {entry}")
    _assign_ids(items)
    overrides = raw.get("overrides")
    if isinstance(overrides, dict):
        apply_user_overrides(
            items, {str(key): value for key, value in overrides.items() if isinstance(value, dict)}
        )
    included = [item for item in items if item.include]
    if not included:
        raise ValueError("Batch request has no included structures")
    return included


__all__ = [
    "BATCH_REQUEST_SCHEMA_VERSION",
    "load_batch_request",
    "load_items_from_result_manifest",
    "load_items_from_xyz_file",
    "load_items_from_xyz_text",
]
