"""Project saved frame candidates as energy-graph annotations.

Loads the ``RESULT/frame_candidates.json`` authority file and converts
each candidate into a ``TrajectoryAnnotation``-shaped dict suitable for
merging into any energy-graph projection.  All output goes through the
``TrajectoryAnnotation`` contract (AGENTS.md anti-pattern #28).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.results.frame_candidate_store import load_authority
from acp.results.frames import TrajectoryAnnotation

logger = logging.getLogger(__name__)

__all__ = ["build_frame_candidate_annotations"]


def build_frame_candidate_annotations(
    task_root: Path,
    *,
    view_type: str,
    item_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build annotation dicts for saved candidates matching *view_type*.

    Args:
        task_root: Job working directory (absolute).
        view_type: One of ``scan``, ``optimization``, ``sampling``,
            ``conformer``.  Only candidates whose ``view_type`` matches
            are projected.
        item_id: When provided, only candidates whose ``item_id``
            matches are included.  When ``None``, all candidates for the
            view_type are included regardless of their item_id.

    Returns:
        A list of annotation dicts (``TrajectoryAnnotation.to_annotation()``
        output).  Empty list when the authority file is missing or no
        candidates match.

    The emitted dict promotes ``candidate_id``, ``saved``, and
    ``selection_source`` to top-level wire keys via
    ``_ANNOTATION_METADATA_KEYS`` in frames.py.
    """
    payload = load_authority(task_root)
    if payload is None:
        return []
    annotations: list[dict[str, Any]] = []
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("view_type") != view_type:
            continue
        # Item scoping: when item_id is provided, filter strictly
        candidate_item_id = candidate.get("item_id")
        if item_id is not None and candidate_item_id != item_id:
            continue
        cid = str(candidate.get("candidate_id") or "")
        role = str(candidate.get("role") or "TS").upper()
        role_idx = int(candidate.get("role_index") or 0)
        display_label = str(candidate.get("display_label") or f"{role}{role_idx}")
        annotation = TrajectoryAnnotation(
            id=f"candidate:{cid}",
            type="ts" if role == "TS" else "intermediate",
            label=display_label,
            frame_index=int(candidate.get("frame_index") or 0),
            x=None,
            y=None,
            metadata={
                "candidate_id": cid,
                "saved": True,
                "selection_source": "manual_frame",
                "role": role,
                "role_index": int(candidate.get("role_index") or 0),
                "item_id": candidate_item_id,
            },
        )
        annotations.append(annotation.to_annotation())
    return annotations
