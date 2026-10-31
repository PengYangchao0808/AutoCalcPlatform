"""Round-6 #2: one IRC direction normalization for every wire form."""

from __future__ import annotations

import pytest

from acp.calculations.irc_directions import normalize_irc_directions


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("both", ["forward", "reverse"]),
        (["both"], ["forward", "reverse"]),
        ("forward", ["forward"]),
        ("reverse", ["reverse"]),
        (["forward", "reverse"], ["forward", "reverse"]),
        (["reverse", "forward"], ["forward", "reverse"]),
        (["forward", "forward"], ["forward"]),
        (" BOTH ", ["forward", "reverse"]),
    ],
)
def test_normalize_irc_directions_accepts_every_documented_form(value, expected) -> None:
    assert normalize_irc_directions(value) == expected


@pytest.mark.parametrize(
    "value", [[], ["both", "forward"], ["sideways"], [1], None, 3, {"both": True}]
)
def test_normalize_irc_directions_refuses_undocumented_forms(value) -> None:
    with pytest.raises(ValueError, match="must be forward, reverse, or both"):
        normalize_irc_directions(value)
