"""XYZ parsing primitives for batch structure inputs."""
# pyright: basic, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnusedCallResult=false

from __future__ import annotations

from pathlib import Path

from acp.calculations.contracts import StructureRole
from acp.intake import parse_xyz_text

from ._items import BatchStructureItem, JsonObject
from ._tag import normalize_tag, parse_tag_comment


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _mapping(value: object) -> JsonObject | None:
    return dict(value) if isinstance(value, dict) else None


def _rows(value: object) -> list[JsonObject]:
    return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _first_comment(text: str) -> str:
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return ""
    try:
        int(lines[0].strip())
    except ValueError:
        return ""
    return lines[1]


def _rewrite_comment(text: str, comment: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return text
    try:
        count = int(lines[0].strip())
    except ValueError:
        return text
    if len(lines) < count + 1:
        return text
    return "\n".join([lines[0], comment, *lines[2 : count + 2]]) + "\n"


def _assign_ids(items: list[BatchStructureItem]) -> list[BatchStructureItem]:
    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        item.item_id = item.item_id or f"item_{index:03d}"
        original = item.item_id
        suffix = 1
        while item.item_id in seen:
            item.item_id = f"{original}_x{suffix}"
            suffix += 1
        seen.add(item.item_id)
        item.candidate_id = item.candidate_id or item.item_id
    return items


def _items_from_text(
    text: str, source_type: str, source_ref: str, base_name: str
) -> list[BatchStructureItem]:
    result = parse_xyz_text(text)
    multiple = len(result.structures) > 1
    items: list[BatchStructureItem] = []
    for index, structure in enumerate(result.structures, 1):
        xyz = str(structure.xyz or "")
        if not xyz:
            continue
        comment = _first_comment(xyz)
        info = parse_tag_comment(comment)
        candidate = info["candidate_id"]
        words = comment.split()
        if candidate is None and words and words[0].lower().startswith(("ts_", "int_")):
            candidate = words[0]
        tag = info["tag"] or normalize_tag(candidate) or "INT"
        name = f"{base_name}__frame_{index:03d}" if multiple else base_name
        items.append(
            BatchStructureItem(
                item_id="",
                name=name,
                tag=tag,
                xyz=xyz,
                candidate_id=candidate or "",
                source_type=source_type,
                source_ref=source_ref,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                atom_count=structure.atom_count,
                formula=structure.formula,
            )
        )
    return _assign_ids(items)


def load_items_from_xyz_text(
    text: str,
    *,
    source_type: str = "paste",
    source_ref: str = "",
    base_name: str = "mol",
) -> list[BatchStructureItem]:
    """Load one item per frame from XYZ text."""
    return _items_from_text(text, source_type, source_ref, base_name)


def load_items_from_xyz_file(
    path: Path | str,
    *,
    source_type: str = "upload",
    tag: str | None = None,
) -> list[BatchStructureItem]:
    """Load one item per frame from an XYZ file."""
    file_path = Path(path)
    items = _items_from_text(
        file_path.read_text(encoding="utf-8"), source_type, str(file_path), file_path.stem
    )
    normalized = normalize_tag(tag)
    if normalized:
        for item in items:
            item.tag = normalized
            item.role = (
                StructureRole.TRANSITION_STATE if normalized == "TS" else StructureRole.MINIMUM
            )
    return items


__all__ = [
    "_assign_ids",
    "_first_comment",
    "_items_from_text",
    "_mapping",
    "_rewrite_comment",
    "_rows",
    "_text",
    "load_items_from_xyz_file",
    "load_items_from_xyz_text",
]
