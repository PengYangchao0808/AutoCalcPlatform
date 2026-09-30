"""Synthetic frequency-source fixtures for TS Mode tests.

Builds internally-consistent ORCA ``.hess`` + frequency ``.out`` pairs with
known eigenvectors so the mapping math can be verified without a real ORCA
installation.  The construction mirrors the physical pipeline: a
mass-weighted Hessian is assembled from chosen eigenvalues/vectors in the
vibrational subspace (translations/rotations projected out), then
unweighted to Cartesian form for the ``.hess`` file.  The printed output
lists the same modes with ``u/sqrt(m)`` Cartesian displacements.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from cccp.utils.constants import BOHR_TO_ANGSTROM

CM1_PER_SQRT = 5140.4871

ELEMENTS = ["C", "C", "O", "H", "H"]
MASSES = np.array([12.0, 12.0, 16.0, 1.008, 1.008])


def build_molecule(seed: int = 7) -> NDArray[np.float64]:
    rng = np.random.default_rng(seed)
    return rng.uniform(-1.5, 1.5, (len(ELEMENTS), 3))


def _external_subspace(coords: NDArray[np.float64], sqrt_masses: NDArray[np.float64]):
    from acp.calculations.tsmode.source import build_external_subspace

    return build_external_subspace(coords, sqrt_masses)


def build_modes(
    coords: NDArray[np.float64],
    frequencies_cm1: list[float],
    seed: int = 11,
) -> tuple[NDArray[np.float64], list[NDArray[np.float64]], NDArray[np.float64]]:
    """Assemble (cartesian_hessian, cart_modes, eigenvalues_ascending).

    The returned Hessian has exactly the requested vibrational frequencies;
    the six external modes are (numerically) zero.
    """
    n_atoms = len(coords)
    dim = 3 * n_atoms
    masses = MASSES[:n_atoms]
    sqrt_masses = np.sqrt(masses)
    weight = np.repeat(sqrt_masses, 3)

    external = _external_subspace(coords, sqrt_masses)
    projector = np.eye(dim) - external @ external.T

    rng = np.random.default_rng(seed)
    k = len(frequencies_cm1)
    raw = projector @ rng.normal(size=(k, dim)).T
    q, _ = np.linalg.qr(raw)
    q = projector @ q

    eigenvalues = np.asarray(
        [np.sign(f) * (abs(f) / CM1_PER_SQRT) ** 2 for f in frequencies_cm1]
    )
    h_mw = q @ np.diag(eigenvalues) @ q.T
    h_mw = projector @ h_mw @ projector
    hessian = h_mw * np.outer(weight, weight)

    cart_modes = [
        (q[:, index] / weight).reshape((n_atoms, 3)) for index in range(k)
    ]
    return hessian, cart_modes, eigenvalues


def write_hess_file(path, coords: NDArray[np.float64], hessian: NDArray[np.float64]) -> None:
    n_atoms = len(coords)
    lines = ["$orca_hessian", "", "$atoms", f" {n_atoms}"]
    for element, mass in zip(ELEMENTS[:n_atoms], MASSES[:n_atoms]):
        lines.append(f" {element:<2s} {mass:.8f}")
    lines.append("$coords")
    lines.append(f" {n_atoms}")
    for element, row in zip(ELEMENTS[:n_atoms], coords / BOHR_TO_ANGSTROM):
        lines.append(f" {element:<2s} {row[0]:.10f} {row[1]:.10f} {row[2]:.10f}")
    lines.append("$hessian")
    dim = hessian.shape[0]
    lines.append(f" {dim}")
    flat = hessian.reshape(-1)
    for start in range(0, len(flat), 5):
        chunk = flat[start : start + 5]
        lines.append(" " + " ".join(f"{value:16.9E}" for value in chunk))
    lines.append("$end")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_out_file(
    path,
    coords: NDArray[np.float64],
    frequencies_by_native: dict[int, float],
    cart_modes_by_native: dict[int, NDArray[np.float64]],
    *,
    charge: int = 0,
    multiplicity: int = 1,
    orca_version: str = "6.0.1",
) -> None:
    """Write an ORCA-style frequency output.

    Modes print in descending frequency order with their native indices;
    the NORMAL MODES matrix columns align with those indices.
    """
    n_atoms = len(coords)
    descending = sorted(
        frequencies_by_native, key=lambda index: -frequencies_by_native[index]
    )
    lines = [
        "  ******************************",
        f"  * Program Version {orca_version} *",
        "  ******************************",
        "! r2SCAN-3c Freq",
        "",
        f"* xyz {charge} {multiplicity}",
    ]
    for element, row in zip(ELEMENTS[:n_atoms], coords):
        lines.append(
            f"{element} {row[0]:.8f} {row[1]:.8f} {row[2]:.8f}"
        )
    lines.append("*")
    lines.append("")
    lines.append("CARTESIAN COORDINATES (ANGSTROEM)")
    lines.append("---------------------------------")
    for index, (element, row) in enumerate(zip(ELEMENTS[:n_atoms], coords)):
        lines.append(
            f"  {index:>3d}  {element:<2s} {row[0]:12.6f} {row[1]:12.6f} {row[2]:12.6f}"
        )
    lines.append("")
    lines.append("VIBRATIONAL FREQUENCIES")
    lines.append("-----------------------")
    for native in descending:
        lines.append(f"   {native}:  {frequencies_by_native[native]:18.2f} cm**-1")
    lines.append("")
    lines.append("NORMAL MODES")
    lines.append("------------")
    for start in range(0, len(descending), 3):
        batch = descending[start : start + 3]
        lines.append("  " + "  ".join(f"{index:>13d}" for index in batch))
        component = 0
        for atom in range(n_atoms):
            for axis in range(3):
                values = [cart_modes_by_native[index][atom, axis] for index in batch]
                lines.append(
                    f"  {component:>3d}  " + "  ".join(f"{value:14.7f}" for value in values)
                )
                component += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_consistent_pair(
    tmp_path,
    frequencies_cm1: list[float] | None = None,
    *,
    seed: int = 7,
    mode_seed: int = 11,
    charge: int = 0,
    multiplicity: int = 1,
):
    """Create a consistent (.out, .hess, bundle-ready) pair under tmp_path.

    Frequencies default to two imaginary + positives; native indices are
    assigned by descending frequency (index 0 = highest frequency).
    Returns (out_path, hess_path, coords, frequencies_by_native,
    cart_modes_by_native).
    """
    frequencies = sorted(
        frequencies_cm1
        or [-520.4, -180.2, 210.5, 480.2, 950.0, 1250.3, 1700.5, 3100.7, 3550.1],
        reverse=True,
    )
    coords = build_molecule(seed)
    hessian, cart_modes, _eigenvalues = build_modes(coords, frequencies, seed=mode_seed)

    frequencies_by_native = {native: freq for native, freq in enumerate(frequencies)}
    cart_modes_by_native = {
        native: cart_modes[position] for position, native in enumerate(range(len(frequencies)))
    }
    out_path = tmp_path / "freq.out"
    hess_path = tmp_path / "freq.hess"
    write_out_file(
        out_path,
        coords,
        frequencies_by_native,
        cart_modes_by_native,
        charge=charge,
        multiplicity=multiplicity,
    )
    write_hess_file(hess_path, coords, hessian)
    return out_path, hess_path, coords, frequencies_by_native, cart_modes_by_native


def eigenvalue_ascending_order(
    frequencies_cm1: list[float],
) -> dict[int, int]:
    """Map native index (descending frequency) → ascending-eigenvalue rank."""
    ascending = sorted(range(len(frequencies_cm1)), key=lambda i: frequencies_cm1[i])
    return {native: rank for rank, native in enumerate(ascending)}
