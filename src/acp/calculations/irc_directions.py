"""Single source for IRC direction wire forms (round-6 complaint #2).

The contract enum advertises ``["forward", "reverse", "both"]``; execution,
attempt freezing and the manager used to each parse it differently, so a
documented ``"both"`` could be refused by one layer and accepted by another.
Every producer normalizes through :func:`normalize_irc_directions`.
"""

from __future__ import annotations

from typing import Any

FORWARD = "forward"
REVERSE = "reverse"
BOTH = "both"

__all__ = ["FORWARD", "REVERSE", "BOTH", "normalize_irc_directions"]


def normalize_irc_directions(value: Any) -> list[str]:
    """Return the ordered forward/reverse pair for any accepted wire form.

    Accepts the scalar ``"both"``, the one-element list ``["both"]``, or a
    non-empty subset of ``forward``/``reverse`` (scalar or list).  The result is
    ordered ``forward`` then ``reverse`` and de-duplicated; anything else raises
    ``ValueError`` with one stable message.
    """
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise ValueError("IRC directions must be forward, reverse, or both")
    normalized = [item.strip().lower() for item in items]
    if not normalized:
        raise ValueError("IRC directions must be forward, reverse, or both")
    if set(normalized) == {BOTH}:
        return [FORWARD, REVERSE]
    if set(normalized) - {FORWARD, REVERSE}:
        raise ValueError("IRC directions must be forward, reverse, or both")
    return [direction for direction in (FORWARD, REVERSE) if direction in normalized]
