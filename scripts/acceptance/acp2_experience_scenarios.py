"""C experience scenario registry (T-06 skeleton; bodies land in T-07).

The harness imports this module from its own directory.  ``SCENARIO_IMPLS``
maps a frozen scenario ID to a callable ``handler(context) -> int``:

* ``0`` = PASS, ``1`` = FAIL, ``2`` = NOT_VERIFIED;
* the handler appends at least one assertion to ``context.assertions`` before a
  PASS is accepted (zero assertions can never PASS);
* artifacts are appended as ``{"path": ...}``; the harness recomputes
  ``size_bytes``/``sha256`` and rejects a missing file.

T-06 deliberately registers no bodies: an unregistered known scenario is
``NOT_VERIFIED`` (exit 2) unless ``--dry-run`` is in effect.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# scenario ID -> handler(context) -> exit code
SCENARIO_IMPLS: dict[str, Callable[[Any], int]] = {}


def register(scenario: str, handler: Callable[[Any], int]) -> Callable[[Any], int]:
    """Register one scenario body; rejects duplicate or empty registrations."""

    if not isinstance(scenario, str) or not scenario:
        raise ValueError("scenario must be a non-empty string")
    if not callable(handler):
        raise TypeError(f"handler for {scenario!r} is not callable")
    if scenario in SCENARIO_IMPLS:
        raise ValueError(f"scenario already registered: {scenario}")
    SCENARIO_IMPLS[scenario] = handler
    return handler
