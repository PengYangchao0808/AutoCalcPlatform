"""Runtime-owner pure helpers (B05-5, CQ-02).

The Runtime owner (control/state/store/execution/recovery) accumulated one
identical copy-paste of the UTC-ISO timestamp helper in every module that
needed a write stamp.  This module is the **single** Runtime-owner definition
of that pure function; every Runtime module imports it (keeping its historical
private alias where callers reference ``_utc_now_iso``).

Scope discipline (CQ-02, plan §29.2)
------------------------------------
* **Owner-scoped, not universal.** This is *not* a universal ``utils.py`` and
  it is *not* importable by the Data or Workspace owners; each owner keeps its
  own helper set.  Merely "same body" is not enough to share — equivalence must
  be shown symbol by symbol.
* **No numeric constants here.** ``HARTREE_TO_KCAL`` / gas constants with
  differing precisions are deliberately *not* merged (C07 decision).
* **No cross-owner private-table access.** This module reads no database and
  imports nothing from ``acp``.

Equivalence of the merged copies (all Runtime owner): every copy was
``datetime.now(timezone.utc).isoformat()`` (the only near-variant,
``jobs._utc_now_iso``, delegated to ``_utc_now().isoformat()`` where
``_utc_now()`` is itself ``datetime.now(timezone.utc)`` — byte-identical
output).  The alias-import below makes every call site resolve to the *same
function object*, so equivalence is by construction.

Author: ACP Services (B lane, todo 29).
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utc_now_iso"]


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Format is exactly ``datetime.now(timezone.utc).isoformat()`` — timezone-aware
    (``+00:00``) with microsecond precision — matching every merged copy.
    """
    return datetime.now(timezone.utc).isoformat()
