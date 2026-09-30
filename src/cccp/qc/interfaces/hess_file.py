"""
ORCA Hessian File (.hess) Reader
================================

Pure parser for ORCA ``.hess`` files (no subprocess).  Extracts the atom
masses, the geometry bound to the Hessian, and the full Cartesian force
constant matrix.  The Hessian in a ``.hess`` file is the *unmass-weighted*
Cartesian force constant matrix in atomic units (Eh / Bohr^2); coordinates
inside the file are in Bohr.

Section layout walked by this reader (unknown sections are skipped):

```
$atoms
<n_atoms>
<symbol> <mass>            (one line per atom)
$coords
<n_atoms>
<symbol> <x> <y> <z>       (Bohr, one line per atom)
$hessian
<dimension = 3*n_atoms>
<matrix values, row-major, wrapped across lines>
$vibrational_frequencies   (optional)
<values, cm**-1>
```

Values may use Fortran ``D`` exponents; both uppercase and lowercase
section markers are accepted.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from cccp.utils.constants import ATOMIC_NUMBER

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^\s*\$\s*([A-Za-z_]+)\s*$")
_NUMBER_RE = re.compile(r"^[-+]?(?:(?:\d*\.?\d+(?:[EeDd][-+]?\d+)?)|nan|inf)$", re.IGNORECASE)

__all__ = [
    "HessFileData",
    "HessFileError",
    "parse_orca_hess_file",
]


class HessFileError(ValueError):
    """Raised when a ``.hess`` file is malformed or truncated."""


def _fortran_float(token: str) -> float:
    return float(token.replace("D", "E").replace("d", "e"))


def _is_number(token: str) -> bool:
    return bool(_NUMBER_RE.match(token))


def _normalize_symbol(token: str) -> str:
    """Normalize an element token (symbol or atomic number) to ``"Xx"`` form."""
    text = token.strip()
    if text.isdigit():
        number = int(text)
        for symbol, atomic_number in ATOMIC_NUMBER.items():
            if atomic_number == number:
                return symbol
        raise HessFileError(f"unknown atomic number {number!r} in .hess file")
    return text[:1].upper() + text[1:].lower()


def _read_tokens(lines: list[str], start: int, count: int) -> tuple[list[str], int]:
    """Read *count* whitespace-separated numeric tokens starting at *start*."""
    tokens: list[str] = []
    offset = start
    while len(tokens) < count and offset < len(lines):
        parts = lines[offset].split()
        for part in parts:
            if _is_number(part):
                tokens.append(part)
            elif len(tokens) < count:
                raise HessFileError(f"expected a numeric token, got {part!r} (line {offset + 1})")
            if len(tokens) == count:
                break
        offset += 1
    if len(tokens) < count:
        raise HessFileError(f"unexpected end of file while reading {count} tokens")
    return tokens, offset


@dataclass(frozen=True)
class HessFileData:
    """Parsed content of one ORCA ``.hess`` file.

    Attributes:
        symbols: Element symbols in file order (normalized ``"Xx"`` form).
        masses_amu: Per-atom masses as written in the file (amu).
        coordinates_bohr: Bound geometry ``(N, 3)`` in Bohr.
        hessian: Cartesian force constant matrix ``(3N, 3N)`` in Eh/Bohr^2.
        vibrational_frequencies: Optional frequency list (cm^-1) when the
            file carries a ``$vibrational_frequencies`` section.
        sections: Names of the sections present in the file (audit trail).
    """

    symbols: list[str]
    masses_amu: NDArray[np.float64]
    coordinates_bohr: NDArray[np.float64]
    hessian: NDArray[np.float64]
    vibrational_frequencies: list[float] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)

    @property
    def n_atoms(self) -> int:
        return len(self.symbols)

    @property
    def dimension(self) -> int:
        return int(self.hessian.shape[0])

    def validate_finite(self) -> None:
        """Raise :class:`HessFileError` when geometry, masses, or Hessian
        contain non-finite values."""
        for name, array in (
            ("masses", self.masses_amu),
            ("coordinates", self.coordinates_bohr),
            ("hessian", self.hessian),
        ):
            if not np.isfinite(array).all():
                raise HessFileError(f"non-finite values in .hess {name} section")


def parse_orca_hess_file(path: str | Path) -> HessFileData:
    """Parse an ORCA ``.hess`` file.

    Args:
        path: File to read.

    Returns:
        :class:`HessFileData` with masses, geometry (Bohr), and the full
        Hessian.

    Raises:
        HessFileError: On missing sections, dimension mismatches, or
            non-finite values.  A Hessian section whose dimension does not
            equal ``3 * n_atoms`` is rejected.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HessFileError(f"cannot read .hess file {file_path}: {exc}") from exc

    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for line in lines:
        match = _SECTION_RE.match(line)
        if match:
            current = match.group(1).lower()
            order.append(current)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)

    for required in ("atoms", "hessian"):
        if required not in sections:
            raise HessFileError(f"missing ${required} section in {file_path}")

    # -- $atoms: count then "<symbol> <mass> [charge]" per atom ----------------
    atoms_lines = sections["atoms"]
    count_tokens, offset = _read_tokens(atoms_lines, 0, 1)
    n_atoms = int(float(count_tokens[0]))
    if n_atoms <= 0:
        raise HessFileError(f"invalid atom count {n_atoms} in {file_path}")
    symbols: list[str] = []
    masses: list[float] = []
    consumed = 0
    index = offset
    while consumed < n_atoms and index < len(atoms_lines):
        parts = atoms_lines[index].split()
        index += 1
        if not parts:
            continue
        if len(parts) < 2 or not _is_number(parts[1]):
            raise HessFileError(f"malformed $atoms line {index!r}: expected '<symbol> <mass>'")
        symbols.append(_normalize_symbol(parts[0]))
        masses.append(_fortran_float(parts[1]))
        consumed += 1
    if consumed != n_atoms:
        raise HessFileError(
            f"$atoms declares {n_atoms} atoms but only {consumed} lines were parsed"
        )

    # -- $coords: count then "<symbol> x y z" per atom (Bohr) ------------------
    coordinates_bohr = np.zeros((n_atoms, 3), dtype=np.float64)
    coords_section = sections.get("coords")
    if coords_section is not None:
        count_tokens, offset = _read_tokens(coords_section, 0, 1)
        if int(float(count_tokens[0])) != n_atoms:
            raise HessFileError(
                f"$coords count {count_tokens[0]} does not match $atoms count {n_atoms}"
            )
        consumed = 0
        index = offset
        while consumed < n_atoms and index < len(coords_section):
            parts = coords_section[index].split()
            index += 1
            if not parts:
                continue
            if len(parts) < 4:
                raise HessFileError(
                    f"malformed $coords line: expected '<symbol> x y z>', got {parts!r}"
                )
            try:
                row = [_fortran_float(part) for part in parts[1:4]]
            except ValueError as exc:
                raise HessFileError(f"malformed $coords numbers {parts!r}") from exc
            coordinates_bohr[consumed] = row
            consumed += 1
        if consumed != n_atoms:
            raise HessFileError(
                f"$coords declares {n_atoms} atoms but only {consumed} lines were parsed"
            )

    # -- $hessian: dimension then 3N*3N values row-major ------------------------
    hess_lines = sections["hessian"]
    dim_tokens, offset = _read_tokens(hess_lines, 0, 1)
    dimension = int(float(dim_tokens[0]))
    if dimension != 3 * n_atoms:
        raise HessFileError(
            f"$hessian dimension {dimension} does not equal 3*{n_atoms} = {3 * n_atoms}"
        )
    values, _ = _read_tokens(hess_lines, offset, dimension * dimension)
    hessian = np.asarray([_fortran_float(token) for token in values], dtype=np.float64).reshape(
        (dimension, dimension)
    )

    # Symmetry enforce: ORCA writes a symmetric matrix; average away the
    # rounding-level asymmetry so downstream eigen-analysis is stable.
    asymmetry = float(np.max(np.abs(hessian - hessian.T))) if dimension else 0.0
    if asymmetry > 1e-4:
        raise HessFileError(f"Hessian is not symmetric (max |H - H^T| = {asymmetry:.3e})")
    hessian = 0.5 * (hessian + hessian.T)

    # -- optional $vibrational_frequencies ------------------------------------
    frequencies: list[float] = []
    freq_section = sections.get("vibrational_frequencies")
    if freq_section:
        for line in freq_section:
            for part in line.split():
                if _is_number(part):
                    frequencies.append(_fortran_float(part))

    data = HessFileData(
        symbols=symbols,
        masses_amu=np.asarray(masses, dtype=np.float64),
        coordinates_bohr=coordinates_bohr,
        hessian=hessian,
        vibrational_frequencies=frequencies,
        sections=order,
    )
    data.validate_finite()
    return data
