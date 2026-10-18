"""Pure xTB-MD trajectory / geometry-metric helpers (T24).

Verbatim relocation of the pure ``acp.workflows.xtbmd_censo_energy`` helpers
into the CCCP analysis domain: title-preserving trajectory parsing, the
GFN-FF kcal/mol title-energy parser, uniform index selection, Kabsch RMSD,
mass-weighted geometry metrics, and per-frame outcome classification.  The
canonical ``(kcal/mol)`` regex is single-sourced from
``cccp.analysis.sampling.models`` (T20).  The historical ACP path re-exports
the same objects.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.analysis.sampling.models import _KCAL_PER_MOL_RE
from cccp.utils.constants import ELEMENT_MASS
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)


_TITLE_ENERGY_RE = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")


def _read_trajectory(traj_xyz: Path) -> tuple[list[str], list[str], list[NDArray[np.float64]]]:
    """Parse a multi-frame XYZ trajectory into symbols / titles / frames.

    Unlike :func:`cccp.utils.file_io.read_xyz_multiframe` (which drops frame
    titles and tolerates truncated rows), this reader keeps the title lines —
    they carry the GFN-FF potential energies used by the equilibration test —
    and raises on structurally inconsistent frames so a corrupt trajectory
    fails fast instead of silently shrinking the ensemble.  Malformed frame
    header lines are skipped with a warning (mirroring the cccp reader).
    """
    text = Path(traj_xyz).read_text(encoding="utf-8")
    lines = text.splitlines()
    symbols: list[str] = []
    titles: list[str] = []
    frames: list[NDArray[np.float64]] = []
    n_atoms: int | None = None
    offset = 0
    while offset < len(lines):
        header = lines[offset].strip()
        if not header:
            offset += 1
            continue
        try:
            atom_count = int(header)
        except ValueError:
            logger.warning("Skipping malformed frame header at line %d: %r", offset, header)
            offset += 1
            continue
        if atom_count == 0:
            if offset == 0:
                raise ValueError(f"Trajectory {traj_xyz} is empty")
            break
        end = offset + 2 + atom_count
        if end > len(lines):
            raise ValueError(
                f"Truncated trajectory frame at line {offset} in {traj_xyz}: "
                f"declared {atom_count} atoms, {len(lines) - offset - 2} coordinate "
                f"lines remain"
            )
        if n_atoms is None:
            n_atoms = atom_count
        elif atom_count != n_atoms:
            raise ValueError(
                f"Atom count inconsistency in {traj_xyz} at line {offset}: "
                f"expected {n_atoms}, got {atom_count}"
            )
        block: list[list[float]] = []
        for line in lines[offset + 2 : end]:
            parts = line.split()
            if len(parts) < 4:
                raise ValueError(
                    f"Malformed coordinate line in {traj_xyz} at frame {len(frames)}: {line!r}"
                )
            if len(symbols) < atom_count:
                symbols.append(parts[0])
            block.append([float(parts[1]), float(parts[2]), float(parts[3])])
        frames.append(np.asarray(block, dtype=np.float64))
        titles.append(lines[offset + 1].strip())
        offset = end
    if not frames:
        raise ValueError(f"Trajectory {traj_xyz} contains no frames")
    return symbols, titles, frames


def _parse_kcal_per_mol(title: str) -> float | None:
    """Return the first ``(kcal/mol)`` value in a trajectory frame title.

    xTB MD titles carry the potential and total energy as
    ``md: <t(ps)> <E_pot> (kcal/mol) <E_tot> (kcal/mol)``; the first
    ``(kcal/mol)`` value is the GFN-FF potential energy used by the
    equilibration test.
    """
    match = _KCAL_PER_MOL_RE.search(title)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _uniform_subsample_indices(n_frames: int, max_frames: int) -> NDArray[np.int64]:
    """Return up to *max_frames* evenly spaced frame indices (keeps order).

    ``max_frames <= 0`` or ``n_frames <= max_frames`` returns all indices.
    """
    if max_frames <= 0 or n_frames <= max_frames:
        return np.arange(n_frames, dtype=np.int64)
    return np.unique(np.round(np.linspace(0, n_frames - 1, max_frames)).astype(np.int64))


def _aligned_rmsd(coords_a: NDArray[np.float64], coords_b: NDArray[np.float64]) -> float:
    """Return the Kabsch-aligned RMSD between two conformations."""
    aligned = GeometryUtils.align_structures(coords_a, coords_b)
    return GeometryUtils.rmsd(coords_a, aligned)


def _radius_of_gyration(coords: NDArray[np.float64], symbols: list[str]) -> float:
    """Return the mass-weighted radius of gyration (Å)."""
    com = GeometryUtils.center_of_mass(coords, symbols)
    shifted = coords - com
    masses = np.asarray([ELEMENT_MASS.get(symbol, 1.0) for symbol in symbols], dtype=np.float64)
    total = masses.sum()
    return float(math.sqrt(float((masses * np.sum(shifted**2, axis=1)).sum() / total)))


def _max_principal_moment(coords: NDArray[np.float64], symbols: list[str]) -> float:
    """Return the largest principal moment of inertia (amu·Å²)."""
    tensor = GeometryUtils.moment_of_inertia(coords, symbols)
    eigenvalues = np.linalg.eigvalsh(tensor)
    return float(eigenvalues[-1])


def _histogram_divergence(values_a: list[float], values_b: list[float], bins: int) -> float:
    """Return 1 − histogram intersection over a shared binning."""
    if not values_a or not values_b:
        return 1.0
    lo = min(min(values_a), min(values_b))
    hi = max(max(values_a), max(values_b))
    if lo == hi:
        return 0.0
    hist_a, _ = np.histogram(values_a, bins=bins, range=(lo, hi))
    hist_b, _ = np.histogram(values_b, bins=bins, range=(lo, hi))
    intersection = float(np.minimum(hist_a, hist_b).sum())
    return 1.0 - intersection / float(max(hist_a.sum(), 1))


def _classify_frame_result(index: int, result: Any) -> tuple[str, float | None, str | None]:
    """Classify a per-frame backend result into (status, energy, reason).

    ``ok`` requires both success and a finite energy; timeouts (detected via
    the error message) are reported as ``timeout`` so the workflow can count
    ``n_timeout`` separately; everything else is ``failed``.
    """
    if result.success and result.energy is not None and math.isfinite(float(result.energy)):
        return "ok", float(result.energy), None
    message = str(result.error_message or "") if result.error_message else ""
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout", None, message or f"frame {index} timed out"
    if result.success:
        return "failed", None, f"frame {index} optimized without a TOTAL ENERGY"
    return "failed", None, message or f"frame {index} optimization failed"


def _parse_title_energy_hartree(title: str) -> float | None:
    """Return the first float in a frame title (GFN1 Hartree energy)."""
    match = _TITLE_ENERGY_RE.search(title)
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _match_isomers_frame(
    frame: NDArray[np.float64],
    iso_stack: NDArray[np.float64],
    tolerance: float,
) -> int | None:
    """Return the isomers frame index that *frame* was copied from.

    ISOSTAT cluster representatives are verbatim members of the isomers
    set, so a vectorized unaligned-RMSD scan finds the source frame
    directly (O(n_atoms) per pair, no SVD); a Kabsch-aligned scan is the
    fallback for symmetry-rotated copies (rare).  ``None`` when no isomers
    frame lies within *tolerance*.
    """
    plain = np.sqrt(np.mean(np.sum((iso_stack - frame[None, :, :]) ** 2, axis=2), axis=1))
    nearest = int(np.argmin(plain))
    if plain[nearest] <= tolerance:
        return nearest
    aligned = [_aligned_rmsd(frame, ref) for ref in iso_stack]
    nearest = int(np.argmin(aligned))
    return nearest if aligned[nearest] <= tolerance else None


def _count_xyz_frames_strict(path: Path) -> int:
    """Return the frame count of a multi-frame XYZ (raises when malformed)."""
    _, _, frames = _read_trajectory(path)
    return len(frames)

__all__ = [
    "_TITLE_ENERGY_RE",
    "_aligned_rmsd",
    "_classify_frame_result",
    "_count_xyz_frames_strict",
    "_histogram_divergence",
    "_match_isomers_frame",
    "_max_principal_moment",
    "_parse_kcal_per_mol",
    "_parse_title_energy_hartree",
    "_radius_of_gyration",
    "_read_trajectory",
    "_uniform_subsample_indices",
]
