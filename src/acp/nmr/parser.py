# pyright: reportMissingTypeStubs=false
"""Parsers for NMR backend output files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TypedDict, cast

from acp.nmr.models import NMRAtomShielding

logger = logging.getLogger(__name__)

_SECTION_HEADERS = (
    "SCF GIAO Magnetic shielding tensor",
    "GIAO Magnetic shielding tensor",
)
_ATOM_HEADER_RE = re.compile(
    r"^\s*(\d+)\s+([A-Za-z]{1,2})\s+Isotropic\s*=\s*([-+]?\d+\.\d+)\s+Anisotropy\s*=\s*([-+]?\d+\.\d+)"
)
_TENSOR_RE = re.compile(r"([XYZ]{2})\s*=\s*([-+]?\d+\.\d+)")


class _AtomParseState(TypedDict):
    """Mutable state for one parsed atom block."""

    atom_index: int
    symbol: str
    isotropic_ppm: float
    anisotropy_ppm: float
    tensor_components_ppm: dict[str, float]


def _normalize_symbol(symbol: str) -> str:
    """Return a normalized element symbol."""
    normalized = symbol.strip()
    if not normalized:
        return normalized
    return normalized[:1].upper() + normalized[1:].lower()


def _is_section_header(line: str) -> bool:
    """Return whether a line starts an NMR shielding section."""
    stripped = line.strip()
    return any(stripped.startswith(header) for header in _SECTION_HEADERS)


def _finalize_atom(atom_data: _AtomParseState) -> NMRAtomShielding:
    """Build an immutable shielding model from parser state."""
    return NMRAtomShielding(
        atom_index=int(atom_data["atom_index"]),
        symbol=str(atom_data["symbol"]),
        isotropic_ppm=float(atom_data["isotropic_ppm"]),
        anisotropy_ppm=float(atom_data["anisotropy_ppm"]),
        tensor_components_ppm=dict(atom_data["tensor_components_ppm"]),
    )


def _validate_expected_symbols(
    shieldings: list[NMRAtomShielding],
    expected_symbols: list[str] | tuple[str, ...] | None,
) -> None:
    """Validate parsed symbols against the expected atom ordering."""
    if expected_symbols is None:
        return

    parsed_symbols = [shielding.symbol for shielding in shieldings]
    normalized_expected = [_normalize_symbol(symbol) for symbol in expected_symbols]

    if len(parsed_symbols) != len(normalized_expected):
        raise ValueError(
            f"Parsed shielding atom count does not match expected symbols: {len(parsed_symbols)} != {len(normalized_expected)}"
        )

    if parsed_symbols != normalized_expected:
        raise ValueError(
            f"Parsed shielding symbols do not match expected symbols: {parsed_symbols} != {normalized_expected}"
        )


def parse_gaussian_nmr_log(
    log_file: str | Path,
    expected_symbols: list[str] | tuple[str, ...] | None = None,
) -> list[NMRAtomShielding]:
    """Parse the last Gaussian GIAO shielding section from a log file."""
    path = Path(log_file)
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    section_start: int | None = None
    for index, line in enumerate(lines):
        if _is_section_header(line):
            section_start = index + 1

    if section_start is None:
        raise ValueError(f"No Gaussian NMR shielding section found in {path}")

    logger.debug("Parsing Gaussian NMR shielding section from %s", path)
    shieldings: list[NMRAtomShielding] = []
    current_atom: _AtomParseState | None = None
    state = "await_atom"

    for line in lines[section_start:]:
        atom_match = _ATOM_HEADER_RE.match(line)
        if atom_match is not None:
            if current_atom is not None:
                shieldings.append(_finalize_atom(current_atom))

            current_atom = {
                "atom_index": int(atom_match.group(1)),
                "symbol": _normalize_symbol(atom_match.group(2)),
                "isotropic_ppm": float(atom_match.group(3)),
                "anisotropy_ppm": float(atom_match.group(4)),
                "tensor_components_ppm": {},
            }
            state = "collect_tensor"
            continue

        if state != "collect_tensor" or current_atom is None:
            continue

        tensor_matches = [
            (str(component), float(value))
            for component, value in cast(list[tuple[str, str]], _TENSOR_RE.findall(line))
        ]
        if not tensor_matches:
            continue

        tensor_components = current_atom["tensor_components_ppm"]
        for component, value in tensor_matches:
            tensor_components[component] = value

    if current_atom is not None:
        shieldings.append(_finalize_atom(current_atom))

    if not shieldings:
        raise ValueError(f"No Gaussian NMR shielding atoms found in {path}")

    _validate_expected_symbols(shieldings, expected_symbols)
    return shieldings


def parse_nmr_output(
    backend_name: str,
    log_file: str | Path,
    expected_symbols: list[str] | tuple[str, ...] | None = None,
) -> list[NMRAtomShielding]:
    """Dispatch NMR output parsing for the requested backend."""
    backend = backend_name.strip().lower()
    if backend in {"gaussian", "gaussian16", "g16"}:
        return parse_gaussian_nmr_log(log_file, expected_symbols=expected_symbols)

    raise ValueError(f"Unsupported NMR backend parser: {backend_name}")


__all__ = ["parse_gaussian_nmr_log", "parse_nmr_output"]
