"""Read-only guidance routes (plan 2026-10-30 §5: 指南发现/读取).

``GET /api/v1/guidance`` returns the compact discovery index;
``GET /api/v1/guidance/{guide_id}`` returns one versioned guide body with a
stable digest for caching.  Both are pure projections of
:mod:`acp.api.guidance_pack` — no manager access, no store writes, no prepare
or fetch side effects (the read-only surface contract of the api package).

The same payloads back the ``acp_get_guidance`` MCP tool through the
Controller client, so HTTP and MCP cannot drift.
"""

from __future__ import annotations

import difflib
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from acp.api.guidance_pack import (
    GUIDE_FORMAT,
    MAX_GUIDE_BYTES,
    PACK_VERSION,
    guidance_index,
    guide_ids,
    read_guide,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/guidance", tags=["guidance"])


@router.get("")
def list_guides() -> dict[str, Any]:
    """Discovery index: one compact card per guide (no bodies)."""
    return guidance_index()


@router.get("/{guide_id}")
def read_guide_route(
    guide_id: str,
    section: str | None = Query(default=None),
    version: str | None = Query(default=None),
    max_bytes: int | None = Query(default=None, ge=1, le=MAX_GUIDE_BYTES),
) -> dict[str, Any]:
    """Read one guide (optionally one section) with explicit omissions."""
    try:
        payload = read_guide(guide_id, section=section, max_bytes=max_bytes)
    except KeyError as exc:
        allowed = list(guide_ids())
        suggestion = difflib.get_close_matches(guide_id, allowed, n=1)
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": f"unknown guide {guide_id!r}",
                "params": {"field": "guide_id", "allowed": allowed},
                **({"suggestion": suggestion[0]} if suggestion else {}),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "input_invalid",
                "message": str(exc),
                "params": {"field": "section", "rule": "unknown_section"},
            },
        ) from exc
    if version is not None and version != payload["version"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_conflict",
                "message": (
                    f"guide {guide_id} is at version {payload['version']}, "
                    f"not the requested {version!r}"
                ),
                "params": {
                    "field": "version",
                    "current_version": payload["version"],
                    "format": GUIDE_FORMAT,
                    "pack_version": PACK_VERSION,
                },
            },
        )
    return payload
