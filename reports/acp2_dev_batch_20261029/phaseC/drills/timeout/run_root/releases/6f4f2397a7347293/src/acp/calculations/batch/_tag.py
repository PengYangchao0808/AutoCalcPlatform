"""Canonical batch structure TAG parsing."""

from __future__ import annotations

import re
from typing import TypedDict

from acp.calculations.contracts import StructureRole

_TAG_RE = re.compile(r"\bTAG\s*[:=]\s*([A-Za-z_ ]+?)(?:\s*\||$)", re.IGNORECASE)
_KV_RES = {
    "candidate_id": re.compile(r"\bcandidate_id\s*=\s*([^\s|]+)", re.IGNORECASE),
    "source": re.compile(r"\bsource\s*=\s*([^\s|]+)", re.IGNORECASE),
    "frame": re.compile(r"\bframe\s*=\s*(\d+)", re.IGNORECASE),
}
_TS_ALIASES = frozenset(
    {"ts", "tst", "ts_seed", "transition_state", "transition state", "transitionstate"}
)
_INT_ALIASES = frozenset({"int", "int_seed", "minimum", "intermediate", "intermediate_seed", "min"})


class TagInfo(TypedDict):
    """Fields extracted from one XYZ comment."""

    tag: str | None
    candidate_id: str | None
    source: str | None
    frame: str | None


def normalize_tag(value: str | StructureRole | None) -> str | None:
    """Normalize a role spelling to ``TS`` or ``INT``."""
    if value is None:
        return None
    text = value.value if isinstance(value, StructureRole) else str(value)
    text = text.strip().lower().replace("-", "_").replace(" ", "_")
    if text in {alias.replace(" ", "_") for alias in _TS_ALIASES}:
        return "TS"
    if text in {alias.replace(" ", "_") for alias in _INT_ALIASES}:
        return "INT"
    return None


def kind_for_tag(tag: str | StructureRole | None) -> str:
    """Return ``ts`` or ``minimum`` for a TAG."""
    return "ts" if normalize_tag(tag) == "TS" else "minimum"


def role_for_tag(tag: str | StructureRole | None) -> str:
    """Return the canonical :class:`StructureRole` value for a TAG."""
    return (
        StructureRole.TRANSITION_STATE.value
        if normalize_tag(tag) == "TS"
        else StructureRole.MINIMUM.value
    )


def tag_for_kind(kind: str | StructureRole | None) -> str:
    """Return ``TS`` or ``INT`` for a kind or role."""
    return "TS" if normalize_tag(kind) == "TS" else "INT"


def parse_tag_comment(comment: str | None) -> TagInfo:
    """Parse the byte-compatible ``TAG: TS|INT | key=value`` contract."""
    text = str(comment or "")
    result = TagInfo(tag=None, candidate_id=None, source=None, frame=None)
    match = _TAG_RE.search(text)
    if match:
        result["tag"] = normalize_tag(match.group(1))
    for key, pattern in _KV_RES.items():
        value = pattern.search(text)
        if value:
            result[key] = value.group(1)  # type: ignore[literal-required]  # dynamic key from _KV_RES matches TagInfo fields
    return result


def build_tag_title(
    tag: str | StructureRole | None,
    *,
    candidate_id: str | None = None,
    source: str | None = None,
    frame: int | None = None,
    extra: str = "",
) -> str:
    """Build the canonical TAG comment line."""
    parts = [f"TAG: {normalize_tag(tag) or 'INT'}"]
    if candidate_id:
        parts.append(f"candidate_id={candidate_id}")
    if source:
        parts.append(f"source={source}")
    if frame is not None:
        parts.append(f"frame={int(frame):03d}")
    if extra:
        parts.append(str(extra))
    return " | ".join(parts)


__all__ = [
    "TagInfo",
    "build_tag_title",
    "kind_for_tag",
    "normalize_tag",
    "parse_tag_comment",
    "role_for_tag",
    "tag_for_kind",
]
