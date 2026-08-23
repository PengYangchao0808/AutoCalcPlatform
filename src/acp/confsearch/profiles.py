"""Confsearch quality profiles (§3.4).

A profile tunes quality knobs *inside* one protocol — MD temperature/time/
seed count, retained conformer count, CENSO level, DFT method/basis — and
must never change the protocol's sampling mechanism. Unknown
(protocol, profile) pairs fall back to the protocol's ``default`` overlay so
a profile name alone can never redirect the sampling route.
"""

from __future__ import annotations

from typing import Any

_PROFILE_OVERLAYS: dict[tuple[str, str], dict[str, Any]] = {
    # ── xtbmd-censo: MD sampling depth + CENSO preset (§3.2.4) ──────────
    ("xtbmd-censo", "light"): {
        "md_time_ps": 50.0,
        "md_seeds": 1,
        "max_frames": 300,
        "preset": "censo-light",
    },
    ("xtbmd-censo", "default"): {
        "md_time_ps": 100.0,
        "md_seeds": 1,
        "max_frames": 500,
        "preset": "censo-light",
    },
    ("xtbmd-censo", "high"): {
        "md_time_ps": 200.0,
        "md_seeds": 3,
        "max_frames": 800,
        "preset": "censo-default",
    },
    # ── xtb-md: same MD axes, pure-xTB energy model (§3.2.2) ────────────
    ("xtb-md", "light"): {"md_time_ps": 50.0, "md_seeds": 1, "max_frames": 300},
    ("xtb-md", "default"): {"md_time_ps": 100.0, "md_seeds": 1, "max_frames": 500},
    ("xtb-md", "high"): {"md_time_ps": 200.0, "md_seeds": 3, "max_frames": 800},
    # ── censo-crest: CENSO funnel depth (§3.2.3) ─────────────────────────
    ("censo-crest", "light"): {"preset": "censo-light"},
    ("censo-crest", "default"): {"preset": "censo-light"},
    ("censo-crest", "high"): {"preset": "censo-default"},
    # ── xtb-crest: CREST window only — single static route (§3.2.1) ──────
    ("xtb-crest", "light"): {"ewin": 6.0},
    ("xtb-crest", "default"): {"ewin": 6.0},
    ("xtb-crest", "high"): {"ewin": 10.0},
}


def profile_overlay(protocol: str, profile: str) -> dict[str, Any]:
    """Return the parameter overlay for (protocol, profile).

    Falls back to the protocol's ``default`` overlay for unknown pairs.
    """
    overlay = _PROFILE_OVERLAYS.get((protocol, profile))
    if overlay is None:
        overlay = _PROFILE_OVERLAYS.get((protocol, "default"), {})
    return dict(overlay)


__all__ = ["profile_overlay"]
