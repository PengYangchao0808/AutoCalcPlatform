"""
ORCA Transition-State Helpers
=============================

Pure input-generation / output-parsing helpers for ORCA transition-state
optimization (OptTS) and IRC runs. No subprocess here — the ``ORCAInterface``
methods in ``orca.py`` own execution.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

_VIBRATIONAL_FREQ_SECTION_HEADER = "VIBRATIONAL FREQUENCIES"
_VIBRATIONAL_FREQ_LINE_RE = re.compile(r"^\s*(\d+):\s+([-+]?\d+\.\d+)\s+cm\*\*-1", re.MULTILINE)
_NORMAL_MODES_SECTION_HEADER = "NORMAL MODES"
_INTEGER_TOKEN_RE = re.compile(r"^\d+$")
_OPT_LEVEL_KEYWORDS = {
    "loose": "LooseOpt",
    "normal": None,
    "tight": "TightOpt",
    "verytight": "VeryTightOpt",
}

# ORCA IRC output files: ``*_IRC_[FB]_trj.xyz`` (per-direction path, energy in
# each frame comment) and ``*_IRC_[FB].xyz`` (endpoint).  ``*_IRC_Full_trj.xyz``
# interleaves both directions with the TS and is deliberately excluded.
_IRC_TRJ_FILE_RE = re.compile(r"_IRC_([FB])_trj\.xyz$", re.IGNORECASE)
_IRC_ENDPOINT_FILE_RE = re.compile(r"_IRC_([FB])\.xyz$", re.IGNORECASE)
_IRC_DIRECTION_BANNER_RE = re.compile(r"\b(FORWARD|BACKWARD|REVERSE)\b.*\bIRC\b", re.IGNORECASE)
_IRC_ITERATION_ROW_RE = re.compile(
    r"^\s*(\d+)\s+([-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?)"
    r"\s+([-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?)"
    r"\s+([-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?)"
    r"\s+([-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?)\s*$"
)
_IRC_ENERGY_COMMENT_RE = re.compile(r"(?:^|\s)E\s+([-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?)")
_IRC_ENERGY_ANCHORED_RE = re.compile(
    r"(?:energy|e)\s*[=:]\s*([-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?)", re.IGNORECASE
)
_IRC_ENERGY_FLOAT_RE = re.compile(r"([-+]?\d+\.\d+(?:[EeDd][-+]?\d+)?)")


@dataclass(frozen=True)
class TsOptResult:
    """Result of an ORCA OptTS (with independent frequency) run.

    Attributes:
        success: Whether a converged TS geometry was produced.
        energy_hartree: Final single-point energy.
        coordinates: Optimized geometry (Å, N×3).
        symbols: Element symbols.
        converged: Whether ORCA terminated normally with a geometry.
        imaginary_frequencies: Frequencies below 0 cm⁻¹.
        all_frequencies: Full frequency list (cm⁻¹).
        output_file: ORCA output path.
        log_file: ORCA log path.
        error_message: Failure note.
        mode_vector: Imaginary mode displacement vectors (N×3) or None.
    """

    success: bool
    energy_hartree: float | None = None
    coordinates: NDArray[np.float64] | None = None
    symbols: list[str] | None = None
    converged: bool = False
    imaginary_frequencies: list[float] = field(default_factory=list)
    all_frequencies: list[float] = field(default_factory=list)
    output_file: Path | None = None
    log_file: Path | None = None
    error_message: str | None = None
    mode_vector: NDArray[np.float64] | None = None

    def has_single_imaginary(self) -> bool:
        return len(self.imaginary_frequencies) == 1

    def lowest_frequency_cm1(self) -> float | None:
        if not self.all_frequencies:
            return None
        return min(self.all_frequencies)


@dataclass(frozen=True)
class IrcResult:
    """Result of an ORCA IRC run.

    Attributes:
        success: Whether IRC ran to completion.
        endpoints: Forward/reverse endpoint file paths.
        forward_points / reverse_points: IRC step counts per direction.
        output_file: ORCA output path.
        log_file: ORCA log path.
        error_message: Failure note.
        final_geometries: Direction → final geometry (Å, N×3).
    """

    success: bool
    endpoints: dict[str, Path] | None = None
    forward_points: int = 0
    reverse_points: int = 0
    output_file: Path | None = None
    log_file: Path | None = None
    error_message: str | None = None
    final_geometries: dict[str, NDArray[np.float64]] = field(default_factory=dict)
    trajectory_files: dict[str, Path] | None = None


@dataclass(frozen=True)
class IrcPathPoint:
    """One point on an ORCA IRC path (geometry + energy, same frame).

    Attributes:
        direction: ``"forward"`` or ``"reverse"``.
        index: 0-based position within the direction, in ORCA calculation order.
        energy_hartree: Frame energy parsed from the trajectory comment.
        coordinates: ``(N, 3)`` geometry in Å.
        symbols: Element symbols for the frame.
        comment: Raw ORCA frame comment.
    """

    direction: str
    index: int
    energy_hartree: float | None = None
    coordinates: NDArray[np.float64] | None = None
    symbols: list[str] = field(default_factory=list)
    comment: str = ""


def _irc_float(value: str) -> float:
    """Parse ORCA's Fortran ``D`` exponent notation."""
    return float(value.replace("D", "E").replace("d", "e"))


def irc_energy_from_comment(comment: str) -> float | None:
    """Extract the path energy embedded in an ORCA IRC frame comment.

    Handles the native ``E -151.527934162309`` form, ``Energy:``/``E =``
    anchors, and finally a lone decimal float.  Returns ``None`` when no
    unambiguous energy is present.
    """
    if not comment:
        return None
    native = _IRC_ENERGY_COMMENT_RE.search(comment)
    if native:
        return _irc_float(native.group(1))
    anchored = _IRC_ENERGY_ANCHORED_RE.search(comment)
    if anchored:
        return _irc_float(anchored.group(1))
    decimals = _IRC_ENERGY_FLOAT_RE.findall(comment)
    if len(decimals) == 1:
        return _irc_float(decimals[0])
    return None


def discover_irc_trajectory_files(work_dir: Path, *, stem: str | None = None) -> dict[str, Path]:
    """Map direction to the best ORCA IRC file found in *work_dir*.

    Per-direction trajectory files (``*_IRC_[FB]_trj.xyz``) are preferred over
    single-frame endpoints (``*_IRC_[FB].xyz``).  ``*_IRC_Full_trj.xyz`` is
    ignored because it interleaves both directions with the TS.  When *stem*
    is given, a file whose name starts with it wins over an unrelated sibling.
    """
    work_dir = Path(work_dir)
    trajectories: dict[str, Path] = {}
    endpoints: dict[str, Path] = {}
    try:
        paths = list(work_dir.glob("*.xyz"))
    except OSError:
        return {}
    for path in paths:
        name = path.name
        trj_match = _IRC_TRJ_FILE_RE.search(name)
        if trj_match:
            direction = "forward" if trj_match.group(1).upper() == "F" else "reverse"
            if direction not in trajectories or (stem and name.startswith(stem)):
                trajectories[direction] = path
            continue
        endpoint_match = _IRC_ENDPOINT_FILE_RE.search(name)
        if endpoint_match:
            direction = "forward" if endpoint_match.group(1).upper() == "F" else "reverse"
            if direction not in endpoints or (stem and name.startswith(stem)):
                endpoints[direction] = path
    chosen: dict[str, Path] = {}
    for direction in ("forward", "reverse"):
        path = trajectories.get(direction) or endpoints.get(direction)
        if path is not None:
            chosen[direction] = path
    return chosen


def parse_irc_trajectory_xyz(path: Path, direction: str) -> list[IrcPathPoint]:
    """Parse a (possibly partial) ORCA IRC trajectory into ordered points.

    Walks standard XYZ blocks and stops at the first incomplete trailing
    frame, so a file being written by a running ORCA job never yields a
    half-read point.  Each complete frame keeps its comment energy and
    geometry together.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("Cannot read IRC trajectory %s", path, exc_info=True)
        return []
    lines = text.splitlines()
    points: list[IrcPathPoint] = []
    offset = 0
    index = 0
    while offset < len(lines):
        header = lines[offset].strip()
        if not header:
            offset += 1
            continue
        try:
            atom_count = int(header)
        except ValueError:
            offset += 1
            continue
        if atom_count <= 0:
            break
        end = offset + 2 + atom_count
        if end > len(lines):
            break
        comment = lines[offset + 1].strip()
        symbols: list[str] = []
        rows: list[list[float]] = []
        complete = True
        for row_index in range(atom_count):
            parts = lines[offset + 2 + row_index].split()
            if len(parts) < 4:
                complete = False
                break
            try:
                rows.append([float(parts[1]), float(parts[2]), float(parts[3])])
            except ValueError:
                complete = False
                break
            symbols.append(str(parts[0]))
        if complete and rows:
            points.append(
                IrcPathPoint(
                    direction=direction,
                    index=index,
                    energy_hartree=irc_energy_from_comment(comment),
                    coordinates=np.asarray(rows, dtype=np.float64),
                    symbols=symbols,
                    comment=comment,
                )
            )
        index += 1
        offset = end
    return points


def parse_irc_iteration_energies(log_text: str) -> dict[str, list[float]]:
    """Extract per-direction IRC iteration energies from an ORCA ``.out``.

    Parses the ``FORWARD IRC`` / ``BACKWARD IRC`` iteration tables as a
    fallback for runs whose trajectory files are missing.  The trailing
    ``IRC PATH SUMMARY`` re-lists every step, so parsing stops there to avoid
    duplicating points into the reverse direction.
    """
    energies: dict[str, list[float]] = {"forward": [], "reverse": []}
    current: str | None = None
    for line in str(log_text).splitlines():
        marker = _IRC_DIRECTION_BANNER_RE.search(line)
        if marker:
            token = marker.group(1).lower()
            current = "reverse" if token in {"backward", "reverse"} else "forward"
            continue
        lowered = line.lower()
        if "irc forward direction" in lowered:
            current = "forward"
            continue
        if "irc reverse direction" in lowered or "irc backward direction" in lowered:
            current = "reverse"
            continue
        if "irc path summary" in lowered or "suggested citations" in lowered:
            current = None
            continue
        if current is None:
            continue
        row = _IRC_ITERATION_ROW_RE.match(line)
        if row:
            try:
                energies[current].append(_irc_float(row.group(2)))
            except ValueError:
                logger.debug("Malformed IRC iteration row: %s", line)
    return energies


def ts_opt_route(
    method: str,
    basis: str = "",
    *,
    grid: str | None = None,
    scf: str | None = None,
    solvent: str | None = None,
    solvent_model: str | None = None,
    nproc: int | None = None,
    aux_j: str | None = None,
    ri_approximation: str | None = None,
    opt_level: str | None = None,
) -> str:
    """Build the ORCA ``!`` route line for an OptTS run.

    Composite 3c methods (``*3c`` suffixes) carry no basis keyword; ordinary
    methods take ``<method> <basis>``. Grid/SCF/solvent keywords are appended
    when provided. ``OptTS`` is always emitted, and ``NumFreq`` is appended
    last so every TS run ends with the independent numerical frequency
    verification. ``opt_level`` accepts
    ``loose`` / ``normal`` / ``tight`` / ``verytight``; ``normal`` leaves the
    route at plain ``OptTS`` because ORCA already uses the default optimization
    thresholds there and emitting a second ``Opt`` run-type keyword would be
    ambiguous. ``%pal nprocs`` is emitted when *nproc* is given.
    """
    method = method.strip()
    tokens = [method]
    is_composite = method.lower().endswith("3c") or basis in ("", None)
    if not is_composite and basis:
        tokens.append(basis)
    if grid:
        tokens.append(grid)
    if scf:
        tokens.append(scf)
    if solvent and solvent_model:
        sm = solvent_model.upper()
        tokens.append(f"{sm}({solvent})")
    tokens.append("OptTS")
    if opt_level is not None:
        level_key = opt_level.strip().lower()
        if level_key not in _OPT_LEVEL_KEYWORDS:
            expected = sorted(_OPT_LEVEL_KEYWORDS)
            raise ValueError(
                f"Unsupported ORCA TS opt_level {opt_level!r}; expected one of {expected}"
            )
        opt_keyword = _OPT_LEVEL_KEYWORDS[level_key]
        if opt_keyword:
            tokens.append(opt_keyword)
    tokens.append("NumFreq")
    route = "! " + " ".join(tokens)
    if aux_j and ri_approximation:
        route += f" {ri_approximation} aux {aux_j}"
    if nproc:
        route += f"\n%pal nprocs {nproc} end"
    return route


def ts_geom_block(
    initial_hessian: str = "calculate",
    recalc_hess: int = 5,
    trust_radius: float = 0.15,
    *,
    ts_mode: bool | int = False,
    max_iter: int | None = None,
) -> str:
    """Render the ``%geom`` block for a TS optimization.

    ``Calc_Hess true`` is emitted only when *initial_hessian* is
    ``"calculate"`` (model/read Hessians omit it). ``trust_radius`` maps to the
    ORCA ``Trust`` keyword (positive value = initial trust radius with
    trust-radius update; negative = fixed). ``max_iter`` emits ORCA's ``MaxIter``
    keyword; ``None`` leaves it out so ORCA's default applies. ``ts_mode`` emits
    ORCA's ``TS_Mode {M n} end`` selector, where *n* is the 0-based normal-mode
    index from the frequency run. ``True`` maps to mode ``0``.
    """
    lines = ["%geom"]
    if initial_hessian == "calculate":
        lines.append("  Calc_Hess true")
    if recalc_hess:
        lines.append(f"  Recalc_Hess {int(recalc_hess)}")
    if trust_radius:
        lines.append(f"  Trust {float(trust_radius):g}")
    if max_iter is not None:
        lines.append(f"  MaxIter {int(max_iter)}")
    emit_ts_mode = False
    mode_index = 0
    if isinstance(ts_mode, bool):
        emit_ts_mode = ts_mode
    else:
        emit_ts_mode = True
        mode_index = int(ts_mode)
    if emit_ts_mode:
        if mode_index < 0:
            raise ValueError(f"ORCA TS_Mode index must be >= 0, got {mode_index}")
        lines.append(f"  TS_Mode {{M {mode_index}}} end")
    lines.append("end")
    return "\n".join(lines)


def freq_block_for_ts() -> str:
    """Extra ``%freq`` settings for a TS run (none — NumFreq rides in the route)."""
    return ""


def irc_route(
    method: str,
    basis: str = "",
    *,
    solvent: str | None = None,
    solvent_model: str | None = None,
) -> str:
    """Build the ORCA ``!`` route line for an IRC run."""
    tokens = [method]
    is_composite = method.lower().endswith("3c") or basis in ("", None)
    if not is_composite and basis:
        tokens.append(basis)
    if solvent and solvent_model:
        sm = solvent_model.upper()
        tokens.append(f"{sm}({solvent})")
    return "! IRC " + " ".join(tokens)


def irc_block(
    direction: str = "both",
    max_iter: int = 100,
    *,
    hess_file_name: str | None = None,
    irc_midpoint_reseed: bool = False,
) -> str:
    """Render the ``%irc`` block.

    *direction* is one of ``"forward"`` / ``"reverse"`` / ``"backward"`` /
    ``"both"``.  When ``irc_midpoint_reseed`` is true the block emits
    ``Direction Down`` so ORCA continues downhill from the supplied midpoint
    geometry without the initial Hessian-based displacement step. ``InitHess
    Read`` is emitted only when a staged ``.hess`` file name is supplied.
    """
    direction_map: dict[str, str] = {
        "forward": "Forward",
        "reverse": "Backward",
        "backward": "Backward",
        "both": "Both",
        "down": "Down",
    }
    if irc_midpoint_reseed:
        orca_direction = "Down"
    else:
        orca_direction = direction_map.get(str(direction).lower(), "Both")
    lines = ["%irc", f"  MaxIter {int(max_iter)}", f"  Direction {orca_direction}"]
    if hess_file_name and not irc_midpoint_reseed:
        lines.append("  InitHess Read")
        lines.append(f'  Hess_Filename "{hess_file_name}"')
    lines.append("end")
    return "\n".join(lines)


def _parse_vibrational_frequency_pairs(log_text: str) -> list[tuple[int, float]]:
    """Extract indexed vibrational frequencies from the final ORCA section."""
    sections = log_text.split(_VIBRATIONAL_FREQ_SECTION_HEADER)
    if len(sections) < 2:
        return []
    pairs = [
        (int(match.group(1)), float(match.group(2)))
        for match in _VIBRATIONAL_FREQ_LINE_RE.finditer(sections[-1])
    ]
    return [(index, freq) for index, freq in pairs if freq != 0.0]


def _parse_frequency_table_pairs(log_text: str) -> list[tuple[int, float]]:
    """Extract indexed frequencies from ORCA's compact table format."""
    indexed: list[tuple[int, float]] = []
    in_table = False
    header_indices: list[int] = []
    next_header_index = 0
    for line in log_text.splitlines():
        stripped = line.strip()
        if "Frequencies in cm**-1" in stripped:
            in_table = True
            header_indices = []
            next_header_index = 0
            continue
        if not in_table or not stripped:
            continue
        parts = stripped.split()
        if all(_INTEGER_TOKEN_RE.fullmatch(part) for part in parts):
            header_indices = [int(part) for part in parts]
            next_header_index = 0
            continue
        if not header_indices:
            continue
        try:
            values = [float(part) for part in parts]
        except ValueError:
            if indexed:
                break
            in_table = False
            header_indices = []
            continue
        for value in values:
            if next_header_index >= len(header_indices):
                break
            mode_index = header_indices[next_header_index]
            if value != 0.0:
                indexed.append((mode_index, value))
            next_header_index += 1
    return indexed


def parse_ts_frequency_map(log_text: str) -> dict[int, float]:
    """Return ``mode_index -> frequency`` for the final ORCA TS frequency set."""
    pairs = _parse_vibrational_frequency_pairs(log_text)
    if not pairs:
        pairs = _parse_frequency_table_pairs(log_text)
    return {mode_index: frequency for mode_index, frequency in pairs}


def parse_ts_frequencies(log_text: str) -> list[float]:
    """Extract vibrational frequencies (cm⁻¹) from an ORCA log.

    Parses the ``Frequencies in cm**-1`` table. The first numeric line after
    the header is the column-index row (0, 1, 2, …) and is skipped; subsequent
    numeric lines carry the actual frequency values.
    """
    return list(parse_ts_frequency_map(log_text).values())


def parse_ts_mode_vectors(log_text: str) -> dict[int, NDArray[np.float64]]:
    """Extract ORCA normal-mode displacement vectors.

    ORCA prints the ``NORMAL MODES`` matrix in column batches (typically six
    modes per batch).  Each row index corresponds to one Cartesian component;
    the returned displacement arrays are reshaped to ``(n_atoms, 3)``.

    Args:
        log_text: Full ORCA output text.

    Returns:
        Mapping of 0-based ORCA mode index to an ``(n_atoms, 3)`` displacement
        array.
    """
    sections = log_text.split(_NORMAL_MODES_SECTION_HEADER)
    if len(sections) < 2:
        return {}

    mode_components: dict[int, list[float]] = {}
    current_modes: list[int] = []
    for line in sections[-1].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        if stripped.startswith(_VIBRATIONAL_FREQ_SECTION_HEADER):
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
            logger.debug("Skipping malformed NORMAL MODES row: %s", stripped)
            continue
        if len(values) != len(current_modes):
            logger.debug(
                "Skipping NORMAL MODES row with %d values for %d active modes: %s",
                len(values),
                len(current_modes),
                stripped,
            )
            continue
        for mode_index, value in zip(current_modes, values):
            mode_components[mode_index].append(value)

    vectors: dict[int, NDArray[np.float64]] = {}
    for mode_index, components in mode_components.items():
        if not components:
            continue
        if len(components) % 3 != 0:
            logger.debug(
                "Skipping NORMAL MODES vector %d with %d components (not divisible by 3)",
                mode_index,
                len(components),
            )
            continue
        vectors[mode_index] = np.asarray(components, dtype=np.float64).reshape((-1, 3))
    return vectors


def parse_irc_endpoints(log_text: str, work_dir: Path) -> dict[str, Path]:
    """Locate IRC endpoint XYZ files produced in *work_dir*.

    Scans for common ORCA endpoint names such as ``<stem>_IRC_F.xyz`` /
    ``<stem>_IRC_B.xyz`` as well as the shorter ``irc_f.xyz`` /
    ``irc_r.xyz``-style files and maps them to ``"forward"`` /
    ``"reverse"``. Returns an empty dict when none are found (endpoint
    discovery then relies on ``final_geometries``).
    """
    _ = log_text
    endpoints: dict[str, Path] = {}
    candidates = {
        "forward": (
            "irc_f.xyz",
            "irc_forward.xyz",
            "ircf.xyz",
        ),
        "reverse": (
            "irc_r.xyz",
            "irc_reverse.xyz",
            "ircr.xyz",
        ),
    }
    for direction, names in candidates.items():
        for name in names:
            path = work_dir / name
            if path.exists():
                endpoints[direction] = path
                break
    if not endpoints:
        pattern = re.compile(r".*irc[_-]([fb]|forward|backward|reverse)\.xyz$", re.IGNORECASE)
        for path in sorted(work_dir.glob("*.xyz")):
            match = pattern.match(path.name)
            if match:
                tag = match.group(1).lower()
                if "forward" not in endpoints and tag in {"f", "forward"}:
                    _ = endpoints.setdefault("forward", path)
                elif "reverse" not in endpoints and tag in {"b", "backward", "reverse"}:
                    _ = endpoints.setdefault("reverse", path)
    return endpoints


def parse_final_energy_hartree(log_text: str) -> float | None:
    """Extract the final single-point energy (Eh) from an ORCA log."""
    for line in log_text.splitlines():
        if "FINAL SINGLE POINT ENERGY" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == "ENERGY" and i + 1 < len(parts):
                    try:
                        return float(parts[i + 1])
                    except ValueError:
                        return None
    return None


__all__ = [
    "IrcPathPoint",
    "IrcResult",
    "TsOptResult",
    "discover_irc_trajectory_files",
    "freq_block_for_ts",
    "irc_block",
    "irc_energy_from_comment",
    "irc_route",
    "parse_final_energy_hartree",
    "parse_irc_endpoints",
    "parse_irc_iteration_energies",
    "parse_irc_trajectory_xyz",
    "parse_ts_frequency_map",
    "parse_ts_frequencies",
    "parse_ts_mode_vectors",
    "ts_geom_block",
    "ts_opt_route",
]
