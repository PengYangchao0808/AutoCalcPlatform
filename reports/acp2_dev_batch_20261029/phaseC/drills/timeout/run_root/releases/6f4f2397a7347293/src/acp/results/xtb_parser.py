"""xTB output parsing (energy + optimization convergence) for design doc §11."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["parse_xtb_energy", "parse_xtb_opt_converged"]

_TOTAL_ENERGY_RE = re.compile(r"TOTAL ENERGY\s+([-+]?\d+\.\d+)\s*Eh")
_XTB_CONVERGED_RE = re.compile(r"HURRAY|GEOMETRY OPTIMIZATION CONVERGED|\bCONVERGED\b")


def _resolve_text(text_or_path: str | Path) -> str:
    """Read *text_or_path*; ``Path`` inputs are read from disk, strings are raw text."""
    if isinstance(text_or_path, Path):
        try:
            return text_or_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("could not read xTB output %s: %s", text_or_path, exc)
            return ""
    return text_or_path


def parse_xtb_energy(text_or_path: str | Path) -> float | None:
    """Final ``TOTAL ENERGY ... Eh`` value (hartree); None when absent."""
    matches = _TOTAL_ENERGY_RE.findall(_resolve_text(text_or_path))
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def parse_xtb_opt_converged(text_or_path: str | Path) -> bool | None:
    """Whether the xTB optimization converged; None when no output text is available.

    Matches uppercase banners (``HURRAY``, ``GEOMETRY OPTIMIZATION CONVERGED``);
    the lowercase SCF line ``converged SCF energy`` does not trigger a match.
    """
    text = _resolve_text(text_or_path)
    if not text:
        return None
    return _XTB_CONVERGED_RE.search(text) is not None
