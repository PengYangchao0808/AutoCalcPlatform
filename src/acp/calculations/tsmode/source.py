"""Frequency-source loading, validation, and vibrational analysis.

Loads a :class:`FrequencySourceBundle` from an ORCA frequency output plus a
matching ``.hess`` file (plan §4).  The Hessian's bound geometry is the
authoritative structure; the output geometry and an optional XYZ are
cross-checked against it with rigid-body (Kabsch) superposition and unit
auto-resolution (Bohr vs Ångström).

Vibrational analysis (:func:`compute_hessian_modes`) mass-weights the
Cartesian force-constant matrix, projects out translations and rotations
(Eckart conditions), and diagonalizes — producing eigen-frequencies and
mass-weighted eigenvectors used by the mode mapping (§6).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from cccp.qc.interfaces.hess_file import HessFileData, HessFileError, parse_orca_hess_file
from cccp.qc.interfaces.orca_ts import (
    parse_ts_frequency_map,
    parse_ts_mode_vectors,
)
from cccp.utils.constants import BOHR_TO_ANGSTROM

from .contracts import (
    FREQUENCY_SOURCE_INCOMPLETE,
    HESSIAN_MISSING,
    SOURCE_GEOMETRY_MISMATCH,
    FrequencySourceBundle,
    SourceLevelOfTheory,
    SourceModeRecord,
    TsmodeError,
    sha256_file,
)

logger = logging.getLogger(__name__)

__all__ = [
    "HessianModes",
    "compute_hessian_modes",
    "kabsch_rmsd",
    "load_bundle_from_files",
    "snapshot_bundle_files",
]

# sqrt(Eh / (amu * a0^2)) in cm^-1 — converts mass-weighted eigenvalues
# (Eh / (a0^2 amu)) to wavenumbers.
_CM1_PER_SQRT_EH_AMU_BOHR2 = 5140.4871

_GEOMETRY_RMSD_TOLERANCE_A = 0.05
# Cross-check tolerance between Hessian-derived frequencies and the printed
# output table. Generous: numerical grids/projection details can shift low
# modes; this is evidence, not a gate (recorded in bundle warnings).
_FREQ_CROSS_CHECK_TOLERANCE_CM1 = 60.0
_MAX_MODES_LIMIT = 200


def kabsch_rmsd(a: NDArray[np.float64], b: NDArray[np.float64]) -> float:
    """RMSD (Å) between two geometries after optimal rotation+translation."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3:
        raise TsmodeError(
            SOURCE_GEOMETRY_MISMATCH,
            f"geometry shapes differ: {a.shape} vs {b.shape}",
        )
    a_centered = a - a.mean(axis=0)
    b_centered = b - b.mean(axis=0)
    covariance = b_centered.T @ a_centered
    u, _, vt = np.linalg.svd(covariance)
    d = np.sign(np.linalg.det(u @ vt))
    correction = np.diag([1.0, 1.0, d])
    rotation = u @ correction @ vt
    aligned = a_centered @ rotation.T
    diff = aligned - b_centered
    return float(np.sqrt((diff * diff).sum() / len(a)))


@dataclass(frozen=True, slots=True)
class HessianModes:
    """Projected vibrational analysis of one Cartesian Hessian.

    Attributes:
        eigenvalues: Ascending eigenvalues of the projected mass-weighted
            Hessian (vibrational subspace only, trans/rot removed).
        eigenvectors: Matching eigenvectors as ``(n_vib, n_atoms, 3)``
            mass-weighted Cartesian displacement arrays.
        frequencies_cm1: Signed wavenumbers (imaginary = negative).
        projector: ``(3N, 3N)`` translation/rotation projector ``P = I-QQᵀ``.
        masses_amu: Per-atom masses used for the weighting.
        n_zero_modes_removed: Number of near-zero eigenvalues discarded.
    """

    eigenvalues: NDArray[np.float64]
    eigenvectors: NDArray[np.float64]
    frequencies_cm1: NDArray[np.float64]
    projector: NDArray[np.float64]
    masses_amu: NDArray[np.float64]
    n_zero_modes_removed: int


def build_external_subspace(
    coordinates_angstrom: NDArray[np.float64],
    sqrt_masses: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Orthonormalized translation+rotation vectors (mass-weighted basis)."""
    n_atoms = len(sqrt_masses)
    dim = 3 * n_atoms
    coords = np.asarray(coordinates_angstrom, dtype=np.float64)
    centered = coords - coords.mean(axis=0)
    basis = np.zeros((6, dim), dtype=np.float64)
    # Translations: sqrt(m_i) along each Cartesian axis.
    for axis in range(3):
        vector = np.zeros((n_atoms, 3), dtype=np.float64)
        vector[:, axis] = sqrt_masses
        basis[axis] = vector.reshape(-1)
    # Rotations about lab axes through the center of mass.
    for index, axis in enumerate(
        (
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
        )
    ):
        lever = np.cross(centered, axis)
        vector = lever * sqrt_masses[:, None]
        basis[3 + index] = vector.reshape(-1)
    # Orthonormalize (columns of Q span the external subspace).
    q, _ = np.linalg.qr(basis.T)
    # Drop numerically degenerate columns (linear molecule: rotation about
    # the molecular axis has zero norm).
    norms = np.linalg.norm(q, axis=0)
    keep = norms > 1e-8
    return q[:, keep]


def compute_hessian_modes(
    hessian: NDArray[np.float64],
    masses_amu: NDArray[np.float64],
    coordinates_angstrom: NDArray[np.float64],
    *,
    zero_threshold_relative: float = 1e-8,
) -> HessianModes:
    """Mass-weight, project, and diagonalize a Cartesian Hessian.

    Args:
        hessian: ``(3N, 3N)`` force constants in Eh/Bohr².
        masses_amu: Per-atom masses (amu).
        coordinates_angstrom: Geometry (Å) used for the rotational vectors.
        zero_threshold_relative: Eigenvalue threshold (relative to the
            largest magnitude eigenvalue) below which projected modes are
            treated as leftover translations/rotations.

    Returns:
        :class:`HessianModes` with eigenvalues ascending.
    """
    hessian = np.asarray(hessian, dtype=np.float64)
    masses = np.asarray(masses_amu, dtype=np.float64)
    coords = np.asarray(coordinates_angstrom, dtype=np.float64)
    n_atoms = len(masses)
    dim = 3 * n_atoms
    if hessian.shape != (dim, dim):
        raise TsmodeError(
            SOURCE_GEOMETRY_MISMATCH,
            f"Hessian shape {hessian.shape} does not match 3×{n_atoms}",
        )
    if not np.isfinite(hessian).all() or np.any(masses <= 0):
        raise TsmodeError(
            FREQUENCY_SOURCE_INCOMPLETE,
            "Hessian or masses contain non-finite/invalid values",
        )

    sqrt_mass = np.sqrt(masses)
    weight = np.repeat(sqrt_mass, 3)
    h_mw = hessian / np.outer(weight, weight)

    external = build_external_subspace(coords, sqrt_mass)
    projector = np.eye(dim) - external @ external.T
    h_projected = projector @ h_mw @ projector

    eigenvalues, eigenvectors = np.linalg.eigh(h_projected)
    # Keep eigenvectors only in the vibrational subspace: components along
    # the external directions are (numerically) zero; re-project so the
    # comparison basis is consistent.
    vib_vectors = (projector @ eigenvectors).T.reshape((-1, n_atoms, 3))

    threshold = zero_threshold_relative * float(np.max(np.abs(eigenvalues)))
    mask = np.abs(eigenvalues) > threshold
    n_removed = int((~mask).sum())
    eigenvalues = eigenvalues[mask]
    vib_vectors = vib_vectors[mask]

    frequencies = np.sign(eigenvalues) * np.sqrt(np.abs(eigenvalues)) * _CM1_PER_SQRT_EH_AMU_BOHR2

    # Normalize each mass-weighted eigenvector to unit length.
    norms = np.linalg.norm(vib_vectors.reshape((len(eigenvalues), -1)), axis=1)
    safe = np.where(norms > 0, norms, 1.0)
    vib_vectors = vib_vectors / safe[:, None, None]

    order = np.argsort(eigenvalues)
    return HessianModes(
        eigenvalues=eigenvalues[order],
        eigenvectors=vib_vectors[order],
        frequencies_cm1=frequencies[order],
        projector=projector,
        masses_amu=masses,
        n_zero_modes_removed=n_removed,
    )


def mass_weight_and_project(
    vectors: NDArray[np.float64],
    masses_amu: NDArray[np.float64],
    coordinates_angstrom: NDArray[np.float64],
    projector: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Mass-weight a Cartesian mode vector and project out trans/rot."""
    vectors = np.asarray(vectors, dtype=np.float64)
    weighted = vectors * np.sqrt(masses_amu)[:, None]
    flat = weighted.reshape(-1)
    projected = projector @ flat
    norm = float(np.linalg.norm(projected))
    if norm <= 0:
        return projected
    return projected / norm


def _parse_output_geometry_angstrom(text: str) -> tuple[list[str], NDArray[np.float64]] | None:
    """Extract the last ORCA Cartesian geometry (Å) from an output text."""
    lines = text.splitlines()
    index = None
    for position in range(len(lines) - 1, -1, -1):
        if "CARTESIAN COORDINATES (ANGSTROEM)" in lines[position]:
            index = position
            break
    if index is None:
        return None
    symbols: list[str] = []
    rows: list[list[float]] = []
    cursor = index + 1
    while cursor < len(lines):
        parts = lines[cursor].strip().split()
        cursor += 1
        if not parts or set(parts[0]) == {"-"}:
            continue
        symbol: str | None = None
        coordinates: list[str] | None = None
        if len(parts) >= 5 and parts[0].lstrip("-").isdigit() and parts[1].isalpha():
            symbol = parts[1]
            coordinates = parts[2:5]
        elif len(parts) >= 4 and parts[0].isalpha():
            symbol = parts[0]
            coordinates = parts[1:4]
        if symbol is None or coordinates is None:
            break
        try:
            rows.append([float(value) for value in coordinates])
        except ValueError:
            break
        symbols.append(symbol)
    if not symbols:
        return None
    return symbols, np.asarray(rows, dtype=np.float64)


def _extract_level_of_theory(text: str) -> SourceLevelOfTheory:
    """Best-effort level-of-theory extraction from an ORCA output header."""
    method = ""
    basis = ""
    orca_version = None
    lines = text.splitlines()
    for line in lines[:400]:
        stripped = line.strip()
        if not orca_version:
            match = re.search(r"Program Version ([0-9][^\s]*)", stripped)
            if match:
                orca_version = match.group(1)
        if stripped.startswith("!"):
            tokens = stripped.lstrip("!").split()
            if tokens:
                method = tokens[0]
                for token in tokens[1:]:
                    if token.lower().endswith("3c") or "def2-" in token.lower():
                        basis = token if not token.lower().endswith("3c") else ""
                        break
    return SourceLevelOfTheory(method=method, basis=basis, orca_version=orca_version)


def load_bundle_from_files(
    output_path: str | Path,
    hess_path: str | Path,
    *,
    geometry_path: str | Path | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    level: SourceLevelOfTheory | None = None,
    bundle_id: str = "",
    origin: dict[str, object] | None = None,
) -> FrequencySourceBundle:
    """Load and validate a frequency source bundle (plan §4.1–§4.3).

    Args:
        output_path: ORCA frequency output (``.out``) with printed modes.
        hess_path: Matching ORCA ``.hess`` file (authoritative geometry +
            Hessian + masses).
        geometry_path: Optional matching XYZ (extra cross-check only).
        charge: System charge; parsed from the output when ``None``.
        multiplicity: Spin multiplicity; parsed from the output when ``None``.
        level: Optional explicit level of theory; best-effort parsed from
            the output when ``None``.
        bundle_id: Stable bundle identifier (generated when empty).
        origin: Provenance fields (job_id / item_id / entry_id / import).

    Returns:
        A validated :class:`FrequencySourceBundle`.

    Raises:
        TsmodeError: With plan §7.3 error codes — ``hessian_missing``,
            ``frequency_source_incomplete``, or ``source_geometry_mismatch``.
    """
    output_file = Path(output_path)
    hess_file = Path(hess_path)
    if not hess_file.is_file():
        raise TsmodeError(HESSIAN_MISSING, f"Hessian file not found: {hess_file}")
    if not output_file.is_file():
        raise TsmodeError(FREQUENCY_SOURCE_INCOMPLETE, f"Frequency output not found: {output_file}")

    try:
        hess_data: HessFileData = parse_orca_hess_file(hess_file)
    except HessFileError as exc:
        raise TsmodeError(HESSIAN_MISSING, f"Unreadable Hessian file: {exc}") from exc

    output_text = output_file.read_text(encoding="utf-8", errors="replace")
    frequency_map = parse_ts_frequency_map(output_text)
    vector_map = parse_ts_mode_vectors(output_text)
    if not frequency_map:
        raise TsmodeError(
            FREQUENCY_SOURCE_INCOMPLETE,
            f"No vibrational frequencies found in {output_file.name}",
        )

    n_atoms = hess_data.n_atoms
    warnings: list[str] = []

    output_geometry = _parse_output_geometry_angstrom(output_text)
    if output_geometry is not None:
        out_symbols, out_coords = output_geometry
        if len(out_symbols) != n_atoms:
            raise TsmodeError(
                SOURCE_GEOMETRY_MISMATCH,
                f"Output geometry has {len(out_symbols)} atoms, Hessian has {n_atoms}",
            )
        normalized_out = [s[:1].upper() + s[1:].lower() for s in out_symbols]
        if normalized_out != hess_data.symbols:
            raise TsmodeError(
                SOURCE_GEOMETRY_MISMATCH,
                "Element order differs between frequency output and Hessian",
            )

    # Unit auto-resolution: .hess coords are documented as Bohr; verify with
    # the output geometry and fall back to Å interpretation when that fits
    # better (record whichever was used).
    hess_coords_bohr = hess_data.coordinates_bohr
    unit_used = "bohr"
    coords_angstrom = hess_coords_bohr * BOHR_TO_ANGSTROM
    if output_geometry is not None:
        rmsd_bohr = kabsch_rmsd(coords_angstrom, output_geometry[1])
        rmsd_angstrom = kabsch_rmsd(hess_coords_bohr, output_geometry[1])
        if rmsd_angstrom + 1e-6 < rmsd_bohr:
            unit_used = "angstrom"
            coords_angstrom = hess_coords_bohr.copy()
            warnings.append(".hess coordinates interpreted as Ångström (better geometry fit)")
        geometry_rmsd = min(rmsd_bohr, rmsd_angstrom)
        if geometry_rmsd > _GEOMETRY_RMSD_TOLERANCE_A:
            raise TsmodeError(
                SOURCE_GEOMETRY_MISMATCH,
                f"Geometry RMSD {geometry_rmsd:.4f} Å exceeds tolerance "
                f"{_GEOMETRY_RMSD_TOLERANCE_A} Å — Hessian does not match the "
                "frequency output",
            )
    if geometry_path is not None and Path(geometry_path).is_file():
        from acp.io.structures import StructureReader  # local import: IO layer

        structure = StructureReader().read(str(geometry_path))
        if structure.coordinates is not None:
            xyz_coords = np.asarray(structure.coordinates, dtype=np.float64)
            if len(xyz_coords) != n_atoms:
                warnings.append("Optional XYZ atom count differs from Hessian")
            else:
                xyz_rmsd = kabsch_rmsd(coords_angstrom, xyz_coords)
                if xyz_rmsd > _GEOMETRY_RMSD_TOLERANCE_A:
                    warnings.append(f"Optional XYZ geometry RMSD {xyz_rmsd:.4f} Å above tolerance")

    modes: list[SourceModeRecord] = []
    for mode_index, frequency in sorted(frequency_map.items()):
        vectors_array = vector_map.get(mode_index)
        if vectors_array is None or vectors_array.shape != (n_atoms, 3):
            warnings.append(f"mode {mode_index}: displacement vectors missing/incomplete")
            continue
        if not np.isfinite(vectors_array).all():
            warnings.append(f"mode {mode_index}: non-finite displacement vectors")
            continue
        modes.append(
            SourceModeRecord(
                source_mode_index=int(mode_index),
                frequency_cm1=float(frequency),
                vectors=[[float(value) for value in row] for row in vectors_array],
            )
        )
    if not modes:
        raise TsmodeError(
            FREQUENCY_SOURCE_INCOMPLETE,
            "No mode with complete displacement vectors — output truncated?",
        )

    # Frequency cross-check against the Hessian eigen-analysis (evidence).
    hessian_modes = compute_hessian_modes(
        hess_data.hessian,
        hess_data.masses_amu,
        coords_angstrom,
    )
    printed = sorted((m.frequency_cm1 for m in modes), reverse=True)
    derived = sorted((float(f) for f in hessian_modes.frequencies_cm1), reverse=True)
    if printed and derived and len(printed) == len(derived):
        worst = max(abs(a - b) for a, b in zip(printed, derived))
        if worst > _FREQ_CROSS_CHECK_TOLERANCE_CM1:
            raise TsmodeError(
                SOURCE_GEOMETRY_MISMATCH,
                f"Hessian-derived frequencies deviate up to {worst:.1f} cm⁻¹ from "
                "the printed output — files do not describe the same calculation",
            )
        if worst > 10.0:
            warnings.append(f"Hessian vs printed frequencies agree within {worst:.1f} cm⁻¹")

    if charge is None:
        charge = _parse_charge(output_text)
    if multiplicity is None:
        multiplicity = _parse_multiplicity(output_text)
    if level is None:
        level = _extract_level_of_theory(output_text)

    hess_sha = sha256_file(hess_file)
    out_sha = sha256_file(output_file)
    bundle_id = bundle_id or f"fb_{hess_sha[:20]}"
    origin_payload: dict[str, object] = dict(origin or {})
    origin_payload.setdefault("kind", "files")

    if len(modes) > _MAX_MODES_LIMIT:
        raise TsmodeError(
            FREQUENCY_SOURCE_INCOMPLETE,
            f"Mode count {len(modes)} exceeds the supported bound {_MAX_MODES_LIMIT}",
        )

    return FrequencySourceBundle(
        bundle_id=bundle_id,
        revision="rev_" + hess_sha[:20],
        origin={str(key): value for key, value in origin_payload.items()},
        elements=list(hess_data.symbols),
        masses_amu=[float(value) for value in hess_data.masses_amu],
        coordinates_angstrom=[[float(value) for value in row] for row in coords_angstrom],
        charge=int(charge),
        multiplicity=int(multiplicity),
        level=level,
        modes=modes,
        hessian_file=str(hess_file),
        hessian_sha256=hess_sha,
        output_file=str(output_file),
        output_sha256=out_sha,
        geometry_file=str(geometry_path) if geometry_path else "",
        geometry_sha256=sha256_file(geometry_path) if geometry_path else "",
        warnings=warnings + ([f"hess coords unit: {unit_used}"] if unit_used != "bohr" else []),
    )


_CHARGE_RE = re.compile(r"Charge\s+(-?\d+)", re.IGNORECASE)
_MULTIPLICITY_RE = re.compile(r"Multiplicity\s+(\d+)", re.IGNORECASE)
_MULTIPLICITY_LINE_RE = re.compile(r"\*\s*xyz\s+(-?\d+)\s+(\d+)")


def _parse_charge(text: str) -> int:
    for line in text.splitlines():
        match = _MULTIPLICITY_LINE_RE.search(line)
        if match:
            return int(match.group(1))
    match = _CHARGE_RE.search(text)
    return int(match.group(1)) if match else 0


def _parse_multiplicity(text: str) -> int:
    for line in text.splitlines():
        match = _MULTIPLICITY_LINE_RE.search(line)
        if match:
            return int(match.group(2))
    match = _MULTIPLICITY_RE.search(text)
    return int(match.group(1)) if match else 1


def snapshot_bundle_files(bundle: FrequencySourceBundle, input_dir: str | Path) -> dict[str, Path]:
    """Copy the bundle's source assets into *input_dir* with stable names.

    Layout (plan §11)::

        INPUT/tsmode/source.xyz        geometry bound to the Hessian
        INPUT/tsmode/source.hess       Hessian copy
        INPUT/tsmode/source_modes.json printed modes + hashes
        INPUT/tsmode/source_bundle.json full bundle snapshot
    """
    import json

    target_dir = Path(input_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    hess_source = Path(bundle.hessian_file)
    if not hess_source.is_file():
        raise TsmodeError(HESSIAN_MISSING, f"Cannot snapshot: Hessian missing at {hess_source}")
    hess_target = target_dir / "source.hess"
    if hess_source.resolve() != hess_target.resolve():
        hess_target.write_bytes(hess_source.read_bytes())

    xyz_lines = [str(bundle.n_atoms), bundle.geo_hash]
    for symbol, row in zip(bundle.elements, bundle.coordinates_angstrom):
        xyz_lines.append(f"{symbol:2s} {row[0]:15.10f} {row[1]:15.10f} {row[2]:15.10f}")
    xyz_target = target_dir / "source.xyz"
    xyz_target.write_text("\n".join(xyz_lines) + "\n", encoding="utf-8")

    modes_payload = {
        "schema_version": "source_modes_v1",
        "geometry_hash": bundle.geo_hash,
        "hessian_sha256": bundle.hessian_sha256,
        "modes": [
            {
                "source_mode_index": mode.source_mode_index,
                "frequency_cm1": mode.frequency_cm1,
                "vectors": mode.vectors,
            }
            for mode in bundle.modes
        ],
    }
    modes_target = target_dir / "source_modes.json"
    modes_target.write_text(json.dumps(modes_payload, indent=2), encoding="utf-8")

    snapshot = bundle.to_dict()
    snapshot["files"]["hessian"]["path"] = "source.hess"
    snapshot["files"]["geometry"]["path"] = "source.xyz"
    bundle_target = target_dir / "source_bundle.json"
    bundle_target.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    return {
        "hessian": hess_target,
        "geometry": xyz_target,
        "modes": modes_target,
        "bundle": bundle_target,
    }


def verify_snapshot_hashes(snapshot_dir: str | Path, bundle: FrequencySourceBundle) -> None:
    """Refuse execution when a staged asset no longer hashes to its source."""
    staged = Path(snapshot_dir) / "source.hess"
    if sha256_file(staged) != bundle.hessian_sha256:
        raise TsmodeError(
            FREQUENCY_SOURCE_INCOMPLETE,
            f"Staged Hessian {staged} hash differs from the bundle snapshot",
        )
