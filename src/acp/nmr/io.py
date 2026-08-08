# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Experimental NMR input parsing (DevDoc §6.2).

Parses the human-readable text format:

    # 13C (ppm), optional atom assignments in parentheses
    C: 167.33(C1), 59.58(C2), 24.50(C3), 157.42(C8)

    # 1H (ppm), optional assignments
    H: 4.81(H4), 7.18(H5), 3.09(H6)

    # equivalence groups (one per line)
    EQ: C10,C12
    EQ: H15,H16

    # atoms to omit (e.g. labile protons)
    OMIT: H19,H51

For unassigned spectra the parenthesized atom labels are omitted and an
optional multiplicity annotation ``2.95(3)`` declares an integral of 3
(e.g. CH3). When omitted, multiplicity defaults to 1.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from acp.nmr.models import ExperimentalNmr, ExperimentalPeak, normalize_symbol

logger = logging.getLogger(__name__)


_PEAK_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*(?:\(\s*([A-Za-z]{1,2}\d+)\s*\))?(?:\((\d+)\))?")
_EQ_LINE_RE = re.compile(r"^(?:EQ|EQUIV)\s*:\s*(.+)$", re.IGNORECASE)
_OMIT_LINE_RE = re.compile(r"^OMIT\s*:\s*(.+)$", re.IGNORECASE)
_NUCLEUS_LINE_RE = re.compile(r"^([A-Za-z]{1,2})\s*:\s*(.+)$")


def _parse_atom_label(raw: str) -> str:
    """Normalize an atom label like ``"c1"`` → ``"C1"``."""
    s = raw.strip()
    if not s:
        return s
    return s[:1].upper() + s[1:].lower() if len(s) == 1 else s[:1].upper() + s[1:]


def _parse_peaks_in_line(element: str, body: str) -> list[ExperimentalPeak]:
    """Parse the comma-separated peak list on a ``C:`` / ``H:`` line."""
    peaks: list[ExperimentalPeak] = []
    sym = normalize_symbol(element)
    for token in body.split(","):
        token = token.strip()
        if not token:
            continue
        m = _PEAK_RE.fullmatch(token)
        if not m:
            logger.warning("Skipping unparseable NMR peak token: %r", token)
            continue
        shift = float(m.group(1))
        atom_label = _parse_atom_label(m.group(2)) if m.group(2) else None
        multiplicity = int(m.group(3)) if m.group(3) else 1
        peaks.append(
            ExperimentalPeak(
                shift_ppm=shift,
                element=sym,
                atom_label=atom_label,
                multiplicity=multiplicity,
            )
        )
    return peaks


def parse_experimental_nmr(content: str | Path) -> ExperimentalNmr:
    """Parse the DevDoc §6.2 text format into :class:`ExperimentalNmr`.

    Args:
        content: Raw text or a path to a file.

    Raises:
        ValueError: When no ``C:`` / ``H:`` / ... nucleus section is found.
    """
    if isinstance(content, Path):
        text = content.read_text(encoding="utf-8")
    else:
        text = str(content)

    peaks: dict[str, list[ExperimentalPeak]] = {}
    equivalence_groups: list[list[str]] = []
    omit_atoms: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        eq_match = _EQ_LINE_RE.match(line)
        if eq_match:
            labels = [_parse_atom_label(t) for t in eq_match.group(1).split(",") if t.strip()]
            if labels:
                equivalence_groups.append(labels)
            continue

        omit_match = _OMIT_LINE_RE.match(line)
        if omit_match:
            for token in omit_match.group(1).split(","):
                token = token.strip()
                if token:
                    omit_atoms.append(_parse_atom_label(token))
            continue

        nuc_match = _NUCLEUS_LINE_RE.match(line)
        if nuc_match:
            element = normalize_symbol(nuc_match.group(1))
            new_peaks = _parse_peaks_in_line(element, nuc_match.group(2))
            if new_peaks:
                peaks.setdefault(element, []).extend(new_peaks)
            continue

        logger.debug("Ignoring unrecognized NMR input line: %r", line)

    if not peaks:
        raise ValueError(
            "Experimental NMR input is empty — expected at least one 'C:' / 'H:' nucleus section."
        )

    assigned = any(
        peak.atom_label is not None for peak_list in peaks.values() for peak in peak_list
    )

    return ExperimentalNmr(
        peaks=peaks,
        equivalence_groups=equivalence_groups,
        omit_atoms=omit_atoms,
        assigned=assigned,
    )


__all__ = ["parse_experimental_nmr"]
