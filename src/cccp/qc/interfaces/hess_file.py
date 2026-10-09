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
<symbol> <mass> <x> <y> <z>      (real ORCA 6.x: coordinates in Bohr;
                                  legacy synthetic files may carry only
                                  "<symbol> <mass> [charge]")

$coords                          (legacy synthetic files only)
<n_atoms>
<symbol> <x> <y> <z>             (Bohr)

$hessian
<dimension = 3*n_atoms>
<column-index header>            (integers, e.g. "0 1 2 3 4")
<row-index> <values...>          (one line per row)

$vibrational_frequencies         (optional)
<count>
<index> <value>                  (exactly count pairs, cm^-1)
```

``$hessian`` block format (real ORCA 6.x, e.g. 6.1.1): the matrix is
written in column blocks.  Every block starts with a *column-index header*
line of integers (not matrix values); each following row line starts with
its *row index* (also not a value), then that row's values for the header's
columns.  Blocks tile the column space — the final block's header may be
narrower than the first block's width (e.g. 3 columns of a 5-wide block).
Row/column indices are validated for range, duplicates, and completeness.
Legacy synthetic files instead wrap the row-major values flat across lines
with no indices; that layout is still accepted (detected by the absence of
an integer header line).

At least one geometry source is required: coordinates embedded in
``$atoms`` rows or a ``$coords`` section.  Files with neither raise
:class:`HessFileError` — zero coordinates are never returned silently.
When both sources are present they are cross-checked for consistency.

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
_INT_RE = re.compile(r"^[+-]?\d+$")

# Cross-check tolerance when a file carries both $atoms coordinates and a
# $coords section (Bohr; the two sections must describe the same geometry).
_GEOM_CROSS_CHECK_TOL_BOHR = 1e-6

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


def _is_int_token(token: str) -> bool:
    return bool(_INT_RE.match(token))


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


def _skip_blanks(lines: list[str], index: int) -> int:
    """Advance past blank and ``#`` comment lines starting at *index*."""
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        break
    return index


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


def _parse_atoms_section(
    lines: list[str], file_path: Path
) -> tuple[list[str], list[float], NDArray[np.float64] | None]:
    """Parse ``$atoms``: count then ``<symbol> <mass> [x y z]`` per atom.

    Real ORCA 6.x rows carry Bohr coordinates after the mass; legacy
    synthetic rows carry only ``<symbol> <mass> [charge]``.

    Args:
        lines: Content lines of the ``$atoms`` section.
        file_path: File being parsed (error messages).

    Returns:
        ``(symbols, masses, coordinates)`` where *coordinates* is the
        ``(N, 3)`` Bohr geometry when every row carries one, else ``None``.

    Raises:
        HessFileError: On count mismatches, malformed rows, or rows that
            mix coordinate-bearing and coordinate-less forms.
    """
    count_tokens, offset = _read_tokens(lines, 0, 1)
    n_atoms = int(float(count_tokens[0]))
    if n_atoms <= 0:
        raise HessFileError(f"invalid atom count {n_atoms} in {file_path}")
    symbols: list[str] = []
    masses: list[float] = []
    coord_rows: list[list[float] | None] = []
    consumed = 0
    index = offset
    while consumed < n_atoms and index < len(lines):
        parts = lines[index].split()
        index += 1
        if not parts:
            continue
        if len(parts) < 2 or not _is_number(parts[1]):
            raise HessFileError(f"malformed $atoms line {index!r}: expected '<symbol> <mass>'")
        if len(parts) == 4:
            raise HessFileError(
                f"malformed $atoms line {index!r}: expected '<symbol> <mass>' or "
                f"'<symbol> <mass> <x> <y> <z>', got {parts!r}"
            )
        symbols.append(_normalize_symbol(parts[0]))
        masses.append(_fortran_float(parts[1]))
        if len(parts) >= 5:
            try:
                coord_rows.append([_fortran_float(token) for token in parts[2:5]])
            except ValueError as exc:
                raise HessFileError(f"malformed $atoms coordinates {parts!r}") from exc
        else:
            coord_rows.append(None)
        consumed += 1
    if consumed != n_atoms:
        raise HessFileError(
            f"$atoms declares {n_atoms} atoms but only {consumed} lines were parsed"
        )
    if all(row is not None for row in coord_rows):
        coordinates: NDArray[np.float64] | None = np.asarray(coord_rows, dtype=np.float64)
    elif all(row is None for row in coord_rows):
        coordinates = None
    else:
        raise HessFileError(
            "$atoms mixes rows with and without coordinates — cannot bind a geometry"
        )
    return symbols, masses, coordinates


def _parse_coords_section(lines: list[str], n_atoms: int) -> NDArray[np.float64]:
    """Parse the legacy ``$coords`` section: count then ``<symbol> x y z``."""
    count_tokens, offset = _read_tokens(lines, 0, 1)
    if int(float(count_tokens[0])) != n_atoms:
        raise HessFileError(
            f"$coords count {count_tokens[0]} does not match $atoms count {n_atoms}"
        )
    coordinates = np.zeros((n_atoms, 3), dtype=np.float64)
    consumed = 0
    index = offset
    while consumed < n_atoms and index < len(lines):
        parts = lines[index].split()
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
        coordinates[consumed] = row
        consumed += 1
    if consumed != n_atoms:
        raise HessFileError(
            f"$coords declares {n_atoms} atoms but only {consumed} lines were parsed"
        )
    return coordinates


def _parse_hessian_blocks(lines: list[str], start: int, dimension: int) -> NDArray[np.float64]:
    """Decode the real ORCA ``$hessian`` block layout into a full matrix.

    Each block is a column-index header line (bare integers, strictly
    increasing, in ``[0, dimension)``) followed by exactly *dimension* row
    lines: ``<row-index> <value>...`` where the values cover the header's
    columns.  The final block's header may be narrower than the first
    block's width.  Every cell must be filled exactly once.

    Args:
        lines: Content lines of the ``$hessian`` section.
        start: Index of the first line after the dimension token.
        dimension: Matrix dimension (``3 * n_atoms``).

    Returns:
        The ``(dimension, dimension)`` matrix.

    Raises:
        HessFileError: On malformed headers, out-of-range/duplicate row
            indices, rows with missing or extra values, truncated blocks,
            non-numeric tokens, or incomplete coverage.  A line of bare
            integers where a data row is expected starts a new block, so a
            block ending early reports missing rows rather than mis-parsing
            the next header as values.
    """
    matrix = np.zeros((dimension, dimension), dtype=np.float64)
    filled = np.zeros((dimension, dimension), dtype=bool)
    index = start
    block_count = 0
    while True:
        index = _skip_blanks(lines, index)
        if index >= len(lines):
            break
        header_tokens = lines[index].split()
        header_line = index + 1
        if not all(_is_int_token(token) for token in header_tokens):
            raise HessFileError(
                f"$hessian: expected a column-index header at line {header_line}, "
                f"got {lines[index]!r}"
            )
        columns = [int(token) for token in header_tokens]
        if columns != sorted(set(columns)):
            raise HessFileError(
                f"$hessian: column-index header at line {header_line} must be strictly "
                f"increasing, got {columns}"
            )
        if columns[-1] >= dimension:
            raise HessFileError(
                f"$hessian: column index out of range in header at line {header_line}: "
                f"{columns} (dimension {dimension})"
            )
        index += 1
        rows_seen: set[int] = set()
        while len(rows_seen) < dimension:
            index = _skip_blanks(lines, index)
            if index >= len(lines):
                raise HessFileError(
                    f"$hessian block for columns {columns[0]}..{columns[-1]}: "
                    f"expected {dimension} rows, got {len(rows_seen)} (section ended)"
                )
            tokens = lines[index].split()
            row_line = index + 1
            if all(_is_int_token(token) for token in tokens):
                raise HessFileError(
                    f"$hessian block for columns {columns[0]}..{columns[-1]}: "
                    f"expected {dimension} rows, got {len(rows_seen)} "
                    f"(line {row_line} starts the next block)"
                )
            index += 1
            if not _is_int_token(tokens[0]):
                raise HessFileError(
                    f"$hessian row index must be an integer, got {tokens[0]!r} (line {row_line})"
                )
            row = int(tokens[0])
            if not 0 <= row < dimension:
                raise HessFileError(
                    f"$hessian row index {row} out of range [0, {dimension}) (line {row_line})"
                )
            if row in rows_seen:
                raise HessFileError(
                    f"$hessian duplicate row index {row} in block for columns "
                    f"{columns[0]}..{columns[-1]} (line {row_line})"
                )
            values = tokens[1:]
            if len(values) != len(columns):
                raise HessFileError(
                    f"$hessian row {row}: expected {len(columns)} values for columns "
                    f"{columns[0]}..{columns[-1]}, got {len(values)} (line {row_line})"
                )
            for column, token in zip(columns, values):
                try:
                    value = _fortran_float(token)
                except ValueError as exc:
                    raise HessFileError(
                        f"$hessian row {row} column {column}: not a number: "
                        f"{token!r} (line {row_line})"
                    ) from exc
                if filled[row, column]:
                    raise HessFileError(
                        f"$hessian duplicate entry at row {row} column {column} (line {row_line})"
                    )
                matrix[row, column] = value
                filled[row, column] = True
            rows_seen.add(row)
        block_count += 1
    if block_count == 0:
        raise HessFileError("$hessian section contains no matrix data")
    if not filled.all():
        missing = int(dimension * dimension) - int(filled.sum())
        raise HessFileError(
            f"$hessian: {missing} of {dimension * dimension} matrix entries missing "
            "(incomplete blocks)"
        )
    return matrix


def _parse_hessian_section(lines: list[str], n_atoms: int, file_path: Path) -> NDArray[np.float64]:
    """Parse ``$hessian``: dimension token, matrix body, symmetry gate.

    The body is decoded in the real ORCA block layout when the first line
    after the dimension is a column-index header (all bare integers),
    otherwise in the legacy flat row-major layout of synthetic files.

    Args:
        lines: Content lines of the ``$hessian`` section.
        n_atoms: Atom count from ``$atoms`` (dimension check basis).
        file_path: File being parsed (error messages).

    Returns:
        The symmetrized ``(3N, 3N)`` matrix.

    Raises:
        HessFileError: On dimension mismatch (``dimension != 3*n_atoms``),
            missing data, malformed blocks/flat tokens, non-finite values,
            or asymmetry above tolerance.
    """
    dim_tokens, offset = _read_tokens(lines, 0, 1)
    dimension = int(float(dim_tokens[0]))
    if dimension != 3 * n_atoms:
        raise HessFileError(
            f"$hessian dimension {dimension} does not equal 3*{n_atoms} = {3 * n_atoms}"
        )
    first = _skip_blanks(lines, offset)
    if first >= len(lines):
        raise HessFileError(f"$hessian section in {file_path} contains no matrix data")
    if all(_is_int_token(token) for token in lines[first].split()):
        hessian = _parse_hessian_blocks(lines, offset, dimension)
    else:
        # Legacy synthetic layout: row-major values wrapped across lines.
        # Real ORCA files never take this branch (their header is all
        # integers) — the block decoder above owns the real format.
        values, _ = _read_tokens(lines, offset, dimension * dimension)
        hessian = np.asarray([_fortran_float(token) for token in values], dtype=np.float64).reshape(
            (dimension, dimension)
        )
    if not np.isfinite(hessian).all():
        raise HessFileError("non-finite values in .hess hessian section")
    # Symmetry enforce: ORCA writes a symmetric matrix; average away the
    # rounding-level asymmetry so downstream eigen-analysis is stable.
    asymmetry = float(np.max(np.abs(hessian - hessian.T))) if dimension else 0.0
    if asymmetry > 1e-4:
        raise HessFileError(f"Hessian is not symmetric (max |H - H^T| = {asymmetry:.3e})")
    return 0.5 * (hessian + hessian.T)


def _parse_frequencies_section(lines: list[str]) -> list[float]:
    """Parse ``$vibrational_frequencies``: count then index/value pairs.

    The count and each mode index are structural tokens — they never enter
    the returned list; values are placed by their index so the six zero
    modes (real files) are kept in order.

    Args:
        lines: Content lines of the section.

    Returns:
        Frequencies (cm^-1) ordered by mode index.

    Raises:
        HessFileError: On a bad count, missing pairs, non-integer or
            out-of-range/duplicate mode indices, or non-numeric values.
    """
    count_tokens, offset = _read_tokens(lines, 0, 1)
    count = int(float(count_tokens[0]))
    if count < 0:
        raise HessFileError(f"invalid $vibrational_frequencies count {count_tokens[0]}")
    if count == 0:
        return []
    tokens, _ = _read_tokens(lines, offset, 2 * count)
    frequencies = [0.0] * count
    seen: set[int] = set()
    for pair in range(count):
        index_token, value_token = tokens[2 * pair], tokens[2 * pair + 1]
        if not _is_int_token(index_token):
            raise HessFileError(
                f"$vibrational_frequencies: mode index must be an integer, "
                f"got {index_token!r} (pair {pair})"
            )
        mode = int(index_token)
        if not 0 <= mode < count:
            raise HessFileError(
                f"$vibrational_frequencies: mode index {mode} out of range [0, {count})"
            )
        if mode in seen:
            raise HessFileError(f"$vibrational_frequencies: duplicate mode index {mode}")
        seen.add(mode)
        try:
            frequencies[mode] = _fortran_float(value_token)
        except ValueError as exc:
            raise HessFileError(
                f"$vibrational_frequencies: invalid value {value_token!r} for mode {mode}"
            ) from exc
    return frequencies


def parse_orca_hess_file(path: str | Path) -> HessFileData:
    """Parse an ORCA ``.hess`` file.

    Args:
        path: File to read.

    Returns:
        :class:`HessFileData` with masses, geometry (Bohr), and the full
        Hessian.

    Raises:
        HessFileError: On missing sections, dimension mismatches, malformed
            block indices (out-of-range/duplicate/missing rows or columns),
            truncated blocks, non-finite values, or when no geometry source
            (``$atoms`` coordinates or ``$coords``) exists.  A Hessian
            section whose dimension does not equal ``3 * n_atoms`` is
            rejected.
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

    # -- $atoms: count then "<symbol> <mass> [x y z]" per atom -----------------
    symbols, masses, atoms_coordinates = _parse_atoms_section(sections["atoms"], file_path)
    n_atoms = len(symbols)

    # -- $coords (legacy synthetic): count then "<symbol> x y z" per atom ------
    coordinates_section: NDArray[np.float64] | None = None
    coords_section = sections.get("coords")
    if coords_section is not None:
        coordinates_section = _parse_coords_section(coords_section, n_atoms)

    # -- $hessian: dimension check + block/flat decode + symmetry gate ---------
    hessian = _parse_hessian_section(sections["hessian"], n_atoms, file_path)

    # -- geometry resolution: require a source, cross-check when both exist ----
    if atoms_coordinates is not None and coordinates_section is not None:
        max_diff = float(np.max(np.abs(atoms_coordinates - coordinates_section)))
        if max_diff > _GEOM_CROSS_CHECK_TOL_BOHR:
            raise HessFileError(
                f"$atoms and $coords geometries disagree in {file_path} "
                f"(max |delta| = {max_diff:.3e} Bohr)"
            )
        coordinates_bohr = coordinates_section
    elif coordinates_section is not None:
        coordinates_bohr = coordinates_section
    elif atoms_coordinates is not None:
        coordinates_bohr = atoms_coordinates
    else:
        raise HessFileError(
            f"no geometry source in {file_path}: $atoms rows carry no coordinates "
            "and no $coords section is present"
        )

    # -- optional $vibrational_frequencies: count + index/value pairs -----------
    frequencies: list[float] = []
    freq_section = sections.get("vibrational_frequencies")
    if freq_section:
        frequencies = _parse_frequencies_section(freq_section)

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
