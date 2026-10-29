"""Dependency-free content digest used by the worker-side step path.

``document_digest`` was already a plain content digest (canonical key order,
compact separators, ``sha256:`` prefix) living in
:mod:`acp_services.runtime.science_binding`.  Importing ``science_binding``
pulls the Data artifact layer (and therefore sqlite) into the worker import
closure, which the worker boundary guard forbids.

This module is the single implementation; ``science_binding.document_digest``
re-exports it (identity forwarder, same object), so no digest semantics change
and no second function appears.  It is a *content* digest, never a science
identity: the step execution identity stays CCCP's.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

__all__ = ["ContentDigestError", "document_digest", "sha256_bytes"]


class ContentDigestError(ValueError):
    """The document cannot be projected to canonical JSON bytes."""


def sha256_bytes(data: bytes) -> str:
    """Return the bare hex sha256 of *data* (no prefix)."""
    return hashlib.sha256(data).hexdigest()


def document_digest(document: Mapping[str, Any]) -> str:
    """Return ``sha256:<hex>`` over the canonical JSON bytes of *document*.

    Canonical key order + compact separators; non-JSON values and NaN/inf are a
    typed :class:`ContentDigestError` instead of a silently different digest.
    """
    try:
        body = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContentDigestError(str(exc)) from exc
    return "sha256:" + sha256_bytes(body)
