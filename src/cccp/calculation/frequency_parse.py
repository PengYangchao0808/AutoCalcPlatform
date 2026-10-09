"""Reusable scientific parse of vibrational-frequency output (plan todo 19).

Single implementation of the frequency / vibration-vector / IR-intensity
scientific parse.  The frequency task returns :class:`FrequencyAnalysis`
directly and the ACP view layer builds ``normal_modes`` products from that
typed data — no second parse of the same QC output exists on the ACP side
(``acp.results.orca_parser`` delegates here; its local regex mirrors are the
documented standalone fallback for a cccp-less environment only).

Semantics (frozen by ``tests/test_acp_frequency_modes.py``):

* ``frequencies`` — final ``VIBRATIONAL FREQUENCIES`` section, line order,
  exact-0.0 modes excluded (the six external modes);
* ``imaginary_frequencies`` — the negative entries of that list;
* ``ir_intensities`` — km/mol, aligned with ``frequencies`` by ORCA mode
  index, ``None`` when any aligned entry is missing;
* ``mode_frequencies`` — ORCA native mode indices **including** zero modes
  (no index compaction); falls back to the compact ``Frequencies in cm**-1``
  table format;
* ``mode_vectors`` — ``(n_atoms, 3)`` displacement tuples per ORCA mode
  index (zero modes kept);
* ``mode_ir_intensities`` — per-mode IR map, ``None`` when no IR section.

The indexed frequency / vectors primitives live in
:mod:`cccp.qc.interfaces.orca_ts`; this module composes them with the list /
IR semantics above.  Module import is stdlib-only (the QC parsers load
lazily) so the typed contracts stay import-pure.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from cccp.calculation.results import FrequencyAnalysis

logger = logging.getLogger(__name__)

__all__ = ["parse_frequency_log", "parse_orca_frequency_text"]

_FREQ_SECTION_HEADER = "VIBRATIONAL FREQUENCIES"
_FREQ_LINE_RE = re.compile(r"^\s*(\d+):\s+([-+]?\d+\.\d+)\s+cm\*\*-1", re.MULTILINE)
_IR_SECTION_HEADER = "IR SPECTRUM"
_IR_LINE_RE = re.compile(r"^\s*(\d+):\s+([-+]?\d+\.\d+)", re.MULTILINE)
_NUMBER_RE = re.compile(r"[-+]?\d+\.?\d*(?:[eE][+-]?\d+)?")

# Realistic parse faults on partial output (never a step failure).
_PARSE_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError)


def parse_orca_frequency_text(text: str) -> FrequencyAnalysis:
    """Parse one ORCA vibrational-frequency output *text* (never raises).

    Returns:
        The typed scientific data.  Missing sections yield empty maps /
        ``None`` fields rather than errors (partial-output resilience).
    """
    frequencies = _frequency_list(text)
    return FrequencyAnalysis(
        frequencies=frequencies,
        imaginary_frequencies=tuple(freq for freq in frequencies if freq < 0.0),
        ir_intensities=_ir_intensity_list(text, frequencies),
        mode_frequencies=_mode_frequency_map(text),
        mode_vectors=_mode_vectors(text),
        mode_ir_intensities=_mode_ir_intensities(text),
    )


def parse_frequency_log(path: Path) -> FrequencyAnalysis | None:
    """Parse the frequency log at *path*; ``None`` when unreadable/empty."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("frequency_parse: could not read %s", path)
        return None
    try:
        return parse_orca_frequency_text(text)
    except _PARSE_ERRORS:
        logger.debug("frequency_parse: failed on %s", path, exc_info=True)
        return None


# ── list semantics (final VIBRATIONAL FREQUENCIES section) ──────────────


def _frequency_list(text: str) -> tuple[float, ...]:
    """Final frequency section, line order, exact-0.0 modes excluded."""
    sections = text.split(_FREQ_SECTION_HEADER)
    if len(sections) < 2:
        return ()
    frequencies: list[float] = []
    for match in _FREQ_LINE_RE.finditer(sections[-1]):
        try:
            freq = float(match.group(2))
        except ValueError:
            continue
        if freq != 0.0:
            frequencies.append(freq)
    return tuple(frequencies)


def _ir_intensity_list(text: str, frequencies: tuple[float, ...]) -> tuple[float, ...] | None:
    """Align IR intensities (km/mol) with *frequencies* by mode index.

    ``None`` when no IR section exists or any frequency's mode lacks an
    entry (best-effort alignment, never partial lists).
    """
    sections = text.split(_IR_SECTION_HEADER)
    if len(sections) < 2:
        return None
    intensities_by_mode = _ir_intensity_map(text)
    if intensities_by_mode is None:
        return None
    freq_sections = text.split(_FREQ_SECTION_HEADER)
    if len(freq_sections) < 2:
        return None
    result: list[float] = []
    for match in _FREQ_LINE_RE.finditer(freq_sections[-1]):
        try:
            freq = float(match.group(2))
        except ValueError:
            continue
        if freq == 0.0:
            continue
        mode = int(match.group(1))
        if mode not in intensities_by_mode:
            return None
        result.append(intensities_by_mode[mode])
    return tuple(result) if result else None


# ── indexed mode maps (ORCA native indices, zero modes kept) ────────────


def _mode_frequency_map(text: str) -> dict[int, float]:
    """``mode_index -> cm⁻¹`` for the final frequency set, zero modes kept.

    Composes the ``orca_ts`` indexed parse (which excludes zero modes and
    falls back to the compact table format) with the zero-inclusive
    ``VIBRATIONAL FREQUENCIES`` pairs so the index map never compacts
    ORCA's native mode indices.
    """
    from cccp.qc.interfaces.orca_ts import parse_ts_frequency_map

    freq_map: dict[int, float] = dict(parse_ts_frequency_map(text))
    for mode_index, freq in _all_frequency_pairs(text).items():
        if mode_index not in freq_map:
            freq_map[mode_index] = freq
    return freq_map


def _all_frequency_pairs(text: str) -> dict[int, float]:
    """All final-section frequency pairs including exact-0.0 entries."""
    result: dict[int, float] = {}
    sections = text.split(_FREQ_SECTION_HEADER)
    if len(sections) < 2:
        return result
    for match in _FREQ_LINE_RE.finditer(sections[-1]):
        try:
            mode_index = int(match.group(1))
            freq = float(match.group(2))
        except (ValueError, IndexError):
            continue
        result[mode_index] = freq
    return result


def _mode_vectors(text: str) -> dict[int, tuple[tuple[float, float, float], ...]]:
    """``mode_index -> (n_atoms, 3)`` displacement tuples (zero modes kept)."""
    from cccp.qc.interfaces.orca_ts import parse_ts_mode_vectors

    raw_vectors = parse_ts_mode_vectors(text)
    mode_vectors: dict[int, tuple[tuple[float, float, float], ...]] = {}
    for mode_index, rows in raw_vectors.items():
        mode_vectors[mode_index] = tuple(
            (float(row[0]), float(row[1]), float(row[2])) for row in rows
        )
    return mode_vectors


def _mode_ir_intensities(text: str) -> dict[int, float] | None:
    """``mode_index -> IR intensity``; ``None`` without an IR section."""
    ir_map = _ir_intensity_map(text)
    return ir_map if ir_map else None


def _ir_intensity_map(text: str) -> dict[int, float] | None:
    """Extract the IR SPECTRUM table into a per-mode map (``None`` if absent).

    ORCA 5.x IR table: ``freq eps T**2 TX TY TZ intensity (rel)`` — the
    intensity is the second-to-last numeric column.
    """
    sections = text.split(_IR_SECTION_HEADER)
    if len(sections) < 2:
        return None
    ir_map: dict[int, float] = {}
    for line in sections[-1].splitlines():
        match = _IR_LINE_RE.match(line)
        if not match:
            continue
        numbers = [float(n) for n in _NUMBER_RE.findall(line[match.start(2) :])]
        if not numbers:
            continue
        try:
            value = numbers[-2] if len(numbers) >= 3 else numbers[-1]
            ir_map[int(match.group(1))] = value
        except (IndexError, ValueError):
            continue
    return ir_map
