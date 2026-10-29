"""ORCA ``.out`` parsing into :class:`OrcaCalculation` records (design doc §11).

Viewer-facing aggregate parser.  The **frequency science** (frequency list,
IR intensities, indexed mode vectors) has exactly one implementation —
:mod:`cccp.calculation.frequency_parse` — and this module delegates to it
(plan todo 19); the local regex mirrors below are the documented standalone
fallback for a cccp-less environment only (same semantics, kept honest by
``tests/test_acp_frequency_modes.py::TestLocalFallbackMirror``).  Energy /
SCF / HOMO-LUMO extraction stays here (viewer aggregates, not frequency
science).  Never raises on partial output — populates whatever markers are
found.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["OrcaCalculation", "OrcaOutputParser"]

_FINAL_ENERGY_RE = re.compile(r"FINAL SINGLE POINT ENERGY\s+([-+]?\d+\.\d+)")
_SCF_CONVERGED_RE = re.compile(r"SCF CONVERGED AFTER\s+(\d+)\s+ITERATIONS")
_SCF_CYCLE_ENERGY_RE = re.compile(r"(?<![A-Za-z-])E=\s*([-+]?\d+\.\d+)")
_OPT_CONVERGED_RE = re.compile(r"THE OPTIMIZATION HAS CONVERGED")
_OPT_FAILED_RE = re.compile(r"THE OPTIMIZATION DID NOT CONVERGE")
_OPT_CYCLE_RE = re.compile(r"^\s*(?:GEOMETRY\s+)?OPTIMIZATION (?:CYCLE|STEP)\s+(\d+)", re.MULTILINE)
_FREQ_SECTION_HEADER = "VIBRATIONAL FREQUENCIES"
_FREQ_LINE_RE = re.compile(r"^\s*(\d+):\s+([-+]?\d+\.\d+)\s+cm\*\*-1", re.MULTILINE)
_IR_SECTION_HEADER = "IR SPECTRUM"
_IR_LINE_RE = re.compile(r"^\s*(\d+):\s+([-+]?\d+\.\d+)", re.MULTILINE)
_NUMBER_RE = re.compile(r"[-+]?\d+\.?\d*(?:[eE][+-]?\d+)?")
_RMS_GRADIENT_RE = re.compile(
    r"(?im)^\s*rms[ -]?grad(?:ient)?\s+(?:\.*\s*)?([-+]?\d+\.?\d*(?:[eE][+-]?\d+)?)"
)
_HOMO_LINE_RE = re.compile(r"(?im)^\s*The\s+HOMO\s+is:\s*([-+]?\d+\.\d+)")
_LUMO_LINE_RE = re.compile(r"(?im)^\s*The\s+LUMO\s+is:\s*([-+]?\d+\.\d+)")
_HOMO_LUMO_GAP_RE = re.compile(r"(?im)^\s*The\s+HOMO-LUMO gap\s+is:\s*([-+]?\d+\.\d+)\s*Eh")
_ORBITAL_SECTION_HEADER = "ORBITAL ENERGIES"
_NORMAL_MODES_SECTION_HEADER = "NORMAL MODES"
_INTEGER_TOKEN_RE = re.compile(r"^\d+$")

# Guarded import of the single scientific frequency parse (plan todo 19).
_CCCP_AVAILABLE = False
_parse_orca_frequency_text = None
try:
    from cccp.calculation.frequency_parse import (
        parse_orca_frequency_text as _parse_orca_frequency_text,
    )

    _CCCP_AVAILABLE = True
except ImportError:
    logger.debug("cccp frequency_parse unavailable; using local fallback")


def _local_parse_frequency_map(log_text: str) -> dict[int, float]:
    """Minimal local mirror of ``parse_ts_frequency_map`` semantics."""
    result: dict[int, float] = {}
    sections = log_text.split(_FREQ_SECTION_HEADER)
    if len(sections) < 2:
        return result
    for match in _FREQ_LINE_RE.finditer(sections[-1]):
        try:
            mode_index = int(match.group(1))
            freq = float(match.group(2))
        except (ValueError, IndexError):
            continue
        if freq != 0.0:
            result[mode_index] = freq
    return result


def _local_parse_all_frequency_pairs(log_text: str) -> dict[int, float]:
    """Parse ALL frequency pairs including zero modes (no filtering).

    Same format as ``_local_parse_frequency_map`` but keeps 0.0 entries
    so that the index map preserves ORCA mode indices without compaction.
    """
    result: dict[int, float] = {}
    sections = log_text.split(_FREQ_SECTION_HEADER)
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


def _local_parse_mode_vectors(log_text: str) -> dict[int, tuple[tuple[float, float, float], ...]]:
    """Minimal local mirror of ``parse_ts_mode_vectors`` semantics (returns tuples)."""
    sections = log_text.split(_NORMAL_MODES_SECTION_HEADER)
    if len(sections) < 2:
        return {}

    mode_components: dict[int, list[float]] = {}
    current_modes: list[int] = []
    for line in sections[-1].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        if stripped.startswith(_NORMAL_MODES_SECTION_HEADER):
            break
        parts = stripped.split()
        if len(parts) < 2:
            continue
        if all(_INTEGER_TOKEN_RE.fullmatch(part) for part in parts):
            current_modes = [int(part) for part in parts]
            for mode_index in current_modes:
                _ = mode_components.setdefault(mode_index, [])
            continue
        if not current_modes or not _INTEGER_TOKEN_RE.fullmatch(parts[0]):
            continue
        try:
            values = [float(part) for part in parts[1:]]
        except ValueError:
            continue
        if len(values) != len(current_modes):
            continue
        for mode_index, value in zip(current_modes, values):
            mode_components[mode_index].append(value)

    vectors: dict[int, tuple[tuple[float, float, float], ...]] = {}
    for mode_index, components in mode_components.items():
        if not components:
            continue
        if len(components) % 3 != 0:
            continue
        tuples: list[tuple[float, float, float]] = []
        for i in range(0, len(components), 3):
            tuples.append((components[i], components[i + 1], components[i + 2]))
        vectors[mode_index] = tuple(tuples)
    return vectors
_ORBITAL_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\d+\.\d+)\s+([-+]?\d+\.\d+)\s+[-+]?\d+\.\d+\s*$", re.MULTILINE
)
# SCF cycle blocks end at these markers; E= lines outside blocks are ignored.
_SCF_BLOCK_END_RE = re.compile(
    r"SCF CONVERGED|SCF NOT CONVERGED|LAST SCF ENERGY|ORBITAL ENERGIES|HURRAY|"
    r"FINAL SINGLE POINT ENERGY|^\s*\*{3}"
)


@dataclass(frozen=True)
class OrcaCalculation:
    """Structured view of one ORCA output file (viewer-facing, §11)."""

    success: bool = False
    final_energy_hartree: float | None = None
    converged: bool = False
    n_opt_cycles: int = 0
    scf_energies: list[float] = field(default_factory=list)
    gradients_rms: list[float] | None = None
    frequencies: list[float] = field(default_factory=list)
    imaginary_modes: list[float] = field(default_factory=list)
    ir_intensities: list[float] | None = None
    homo_hartree: float | None = None
    lumo_hartree: float | None = None
    mode_frequencies: dict[int, float] = field(default_factory=dict)
    mode_vectors: dict[int, tuple[tuple[float, float, float], ...]] = field(default_factory=dict)
    mode_ir_intensities: dict[int, float] | None = None


class OrcaOutputParser:
    """Regex-driven ORCA 5.x ``.out`` parser; side-effect-free (path in → data out)."""

    def parse(self, path: Path) -> OrcaCalculation:
        """Parse *path*; unreadable/missing files yield an all-default record."""
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("could not read ORCA output %s: %s", path, exc)
            text = ""
        return self.parse_text(text)

    def parse_text(self, text: str) -> OrcaCalculation:
        """Parse ORCA output *text*; never raises on partial content."""
        final_energy = self._last_match(_FINAL_ENERGY_RE, text)
        scf_converged = _SCF_CONVERGED_RE.search(text) is not None
        opt_converged = _OPT_CONVERGED_RE.search(text) is not None
        opt_failed = _OPT_FAILED_RE.search(text) is not None
        n_opt_cycles = self._count_opt_cycles(text)
        is_opt_job = bool(n_opt_cycles or opt_converged or opt_failed)
        job_done = "ORCA-CHEMISTRY JOB DONE" in text
        homo, lumo = self._parse_homo_lumo(text)
        frequencies, imaginary, ir_intensities, mode_freqs, mode_vecs, mode_ir = (
            _frequency_fields(text)
        )
        return OrcaCalculation(
            success=final_energy is not None or job_done or scf_converged or opt_converged,
            final_energy_hartree=final_energy,
            converged=opt_converged if is_opt_job else scf_converged,
            n_opt_cycles=n_opt_cycles,
            scf_energies=self._parse_scf_cycle_energies(text),
            gradients_rms=self._parse_rms_gradients(text),
            frequencies=frequencies,
            imaginary_modes=imaginary,
            ir_intensities=ir_intensities,
            homo_hartree=homo,
            lumo_hartree=lumo,
            mode_frequencies=mode_freqs,
            mode_vectors=mode_vecs,
            mode_ir_intensities=mode_ir,
        )

    @staticmethod
    def _last_match(pattern: re.Pattern[str], text: str) -> float | None:
        matches = pattern.findall(text)
        if not matches:
            return None
        try:
            return float(matches[-1])
        except ValueError:
            return None

    @staticmethod
    def _count_opt_cycles(text: str) -> int:
        numbers = [int(n) for n in _OPT_CYCLE_RE.findall(text)]
        return max(numbers) if numbers else 0

    @staticmethod
    def _parse_scf_cycle_energies(text: str) -> list[float]:
        """Collect per-cycle ``E=`` values inside SCF iteration blocks (best effort)."""
        energies: list[float] = []
        in_block = False
        for line in text.splitlines():
            if "SCF ITERATIONS" in line:
                in_block = True
                continue
            if in_block and _SCF_BLOCK_END_RE.search(line):
                in_block = False
                continue
            if not in_block:
                continue
            match = _SCF_CYCLE_ENERGY_RE.search(line)
            if match:
                try:
                    energies.append(float(match.group(1)))
                except ValueError:
                    continue
        return energies

    @staticmethod
    def _parse_rms_gradients(text: str) -> list[float] | None:
        values = []
        for raw in _RMS_GRADIENT_RE.findall(text):
            try:
                values.append(float(raw))
            except ValueError:
                continue
        return values or None

    @staticmethod
    def _parse_frequencies(text: str) -> tuple[list[float], list[float]]:
        """Parse the final vibrational frequency section (last section wins)."""
        sections = text.split(_FREQ_SECTION_HEADER)
        if len(sections) < 2:
            return [], []
        frequencies: list[float] = []
        for match in _FREQ_LINE_RE.finditer(sections[-1]):
            try:
                freq = float(match.group(2))
            except ValueError:
                continue
            if freq != 0.0:  # exclude the 6 translational/rotational zero modes
                frequencies.append(freq)
        return frequencies, [f for f in frequencies if f < 0.0]

    @staticmethod
    def _parse_ir_intensities(text: str, frequencies: list[float]) -> list[float] | None:
        """Align IR intensities (km/mol) with *frequencies* by mode index (best effort)."""
        sections = text.split(_IR_SECTION_HEADER)
        if len(sections) < 2:
            return None
        intensities_by_mode: dict[int, float] = {}
        for line in sections[-1].splitlines():
            match = _IR_LINE_RE.match(line)
            if not match:
                continue
            numbers = [float(n) for n in _NUMBER_RE.findall(line[match.start(2) :])]
            if not numbers:
                continue
            # ORCA 5.x IR table: freq eps T**2 TX TY TZ intensity (rel) —
            # intensity is the second-to-last numeric column.
            try:
                value = numbers[-2] if len(numbers) >= 3 else numbers[-1]
                intensities_by_mode[int(match.group(1))] = value
            except (IndexError, ValueError):
                continue
        sections = text.split(_FREQ_SECTION_HEADER)
        if len(sections) < 2:
            return None
        result: list[float] = []
        for match in _FREQ_LINE_RE.finditer(sections[-1]):
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
        return result if result else None

    @staticmethod
    def _parse_homo_lumo(text: str) -> tuple[float | None, float | None]:
        """HOMO/LUMO in Eh via explicit lines, else the ORBITAL ENERGIES table."""
        homo = _float_or_none(_HOMO_LINE_RE.search(text))
        lumo = _float_or_none(_LUMO_LINE_RE.search(text))
        if homo is None or lumo is None:
            table_homo, table_lumo = _parse_orbital_table(text)
            homo = homo if homo is not None else table_homo
            lumo = lumo if lumo is not None else table_lumo
        gap = _float_or_none(_HOMO_LUMO_GAP_RE.search(text))
        if gap is not None:
            if homo is not None and lumo is None:
                lumo = homo + gap
            elif lumo is not None and homo is None:
                homo = lumo - gap
        return homo, lumo

def _frequency_fields(
    text: str,
) -> tuple[
    list[float],
    list[float],
    list[float] | None,
    dict[int, float],
    dict[int, tuple[tuple[float, float, float], ...]],
    dict[int, float] | None,
]:
    """All frequency-science fields in one parse (plan todo 19).

    Primary path delegates to :func:`cccp.calculation.frequency_parse.parse_orca_frequency_text`
    (the single implementation); the local mirrors are the documented
    standalone fallback when cccp is unavailable.

    Returns:
        ``(frequencies, imaginary_modes, ir_intensities, mode_frequencies,
        mode_vectors, mode_ir_intensities)``.
    """
    if _CCCP_AVAILABLE and _parse_orca_frequency_text is not None:
        try:
            analysis = _parse_orca_frequency_text(text)
        except Exception:  # noqa: BLE001 — viewer parse never raises
            logger.debug("cccp frequency_parse failed; using local fallback", exc_info=True)
        else:
            return (
                list(analysis.frequencies),
                list(analysis.imaginary_frequencies),
                list(analysis.ir_intensities) if analysis.ir_intensities is not None else None,
                dict(analysis.mode_frequencies),
                dict(analysis.mode_vectors),
                dict(analysis.mode_ir_intensities) if analysis.mode_ir_intensities else None,
            )
    return _local_frequency_fields(text)


def _local_frequency_fields(
    text: str,
) -> tuple[
    list[float],
    list[float],
    list[float] | None,
    dict[int, float],
    dict[int, tuple[tuple[float, float, float], ...]],
    dict[int, float] | None,
]:
    """Standalone (cccp-less) mirror of the frequency-science semantics."""
    frequencies, imaginary = OrcaOutputParser._parse_frequencies(text)
    ir_intensities = OrcaOutputParser._parse_ir_intensities(text, frequencies)

    freq_map = _local_parse_frequency_map(text)
    for mode_idx, freq_val in _local_parse_all_frequency_pairs(text).items():
        if mode_idx not in freq_map:
            freq_map[mode_idx] = freq_val
    mode_vectors = _local_parse_mode_vectors(text)

    mode_ir: dict[int, float] | None = None
    ir_sections = text.split(_IR_SECTION_HEADER)
    if len(ir_sections) >= 2:
        ir_map: dict[int, float] = {}
        for line in ir_sections[-1].splitlines():
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
        if ir_map:
            mode_ir = ir_map

    return frequencies, imaginary, ir_intensities, freq_map, mode_vectors, mode_ir


def _float_or_none(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_orbital_table(text: str) -> tuple[float | None, float | None]:
    """HOMO = last occupied row, LUMO = first virtual row of the last table."""
    sections = text.split(_ORBITAL_SECTION_HEADER)
    if len(sections) < 2:
        return None, None
    homo: float | None = None
    lumo: float | None = None
    for match in _ORBITAL_ROW_RE.finditer(sections[-1]):
        try:
            occupancy, energy = float(match.group(2)), float(match.group(3))
        except ValueError:
            continue
        if occupancy >= 0.5:
            homo = energy
        elif lumo is None:
            lumo = energy
    return homo, lumo
