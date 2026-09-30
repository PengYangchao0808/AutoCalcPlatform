"""Regression tests for GeometryUtils.calculate_dihedral (standard IUPAC convention).

The pre-fix implementation built ``b1`` as ``coords[atom_j] - coords[atom_i]``,
a 180-degree phase offset from the standard i-j-k-l torsion convention
(measured_old = standard + 180). This suite pins the fixed convention against
synthetic planar geometries with known torsion values and against an
independent RDKit reference (``rdMolTransforms.GetDihedralDeg``), and locks the
permutation semantics: full reversal (l,k,j,i) preserves the angle, while an
endpoint swap keeping the j-k axis (l,j,k,i) negates it.
"""

from __future__ import annotations

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import rdMolTransforms

from cccp.utils.geometry_tools import GeometryUtils


def _periodic_diff(a_deg: float, b_deg: float) -> float:
    """Signed periodic difference a - b folded into (-180, 180].

    Makes +180 and -180 equivalent (both fold to -180 / 0 difference).
    """
    return ((a_deg - b_deg + 180.0) % 360.0) - 180.0


def _coords_for_dihedral(phi_deg: float) -> np.ndarray:
    """Build a 4-atom geometry whose i-j-k-l torsion is exactly ``phi_deg``.

    Layout: j at origin, k at (1, 0, 0) so the j-k axis is +x; i at (0, 1, 0)
    in the xy-plane; l at (1, cos(phi), sin(phi)) rotated about the j-k axis.
    With the standard convention the measured torsion equals phi directly.
    """
    phi = np.radians(phi_deg)
    return np.array(
        [
            [0.0, 1.0, 0.0],  # i
            [0.0, 0.0, 0.0],  # j
            [1.0, 0.0, 0.0],  # k
            [1.0, float(np.cos(phi)), float(np.sin(phi))],  # l
        ],
        dtype=float,
    )


def _rdkit_dihedral(coords: np.ndarray, i: int, j: int, k: int, l: int) -> float:
    """Independent reference dihedral via RDKit.

    Verified against the installed RDKit (2026.03.6)::

        GetDihedralDeg( (Conformer)conf, (int)iAtomId, (int)jAtomId,
                        (int)kAtomId, (int)lAtomId) -> float
        Returns the dihedral angle in degrees between atoms i, j, k, l

    i.e. the argument order is (conf, i, j, k, l) matching the i-j-k-l
    torsion convention of ``GeometryUtils.calculate_dihedral(coords, i, j, k, l)``.
    """
    mol = Chem.RWMol()
    for _ in range(len(coords)):
        mol.AddAtom(Chem.Atom(6))
    conf = Chem.Conformer(len(coords))
    for idx, (x, y, z) in enumerate(coords):
        conf.SetAtomPosition(idx, (float(x), float(y), float(z)))
    mol.AddConformer(conf)
    return float(rdMolTransforms.GetDihedralDeg(mol.GetConformer(), i, j, k, l))


# Synthetic planar cases: cis (0), trans (+-180), +-30, +-150.
_PLANAR_CASES = [
    (0.0, 0.0),  # cis
    (180.0, 180.0),  # trans
    (-180.0, -180.0),  # trans (negative branch)
    (30.0, 30.0),
    (-30.0, -30.0),
    (150.0, 150.0),
    (-150.0, -150.0),
]


@pytest.mark.parametrize("phi_deg,expected", _PLANAR_CASES)
def test_planar_synthetic_angles(phi_deg: float, expected: float) -> None:
    """Synthetic 4-atom planar torsions measure the expected standard value."""
    coords = _coords_for_dihedral(phi_deg)
    measured = GeometryUtils.calculate_dihedral(coords, 0, 1, 2, 3)
    assert abs(_periodic_diff(measured, expected)) < 1e-9


@pytest.mark.parametrize(
    "phi_deg",
    [0.0, 30.0, -30.0, 150.0, -150.0, 180.0, -180.0, 90.0, -90.0, 17.5, 123.4],
)
def test_matches_rdkit_reference(phi_deg: float) -> None:
    """Fixed calculate_dihedral agrees with RDKit GetDihedralDeg to 1e-9 deg.

    Comparison uses the periodic difference ((d + 180) % 360) - 180 so that
    +180 and -180 are equivalent.
    """
    coords = _coords_for_dihedral(phi_deg)
    ours = GeometryUtils.calculate_dihedral(coords, 0, 1, 2, 3)
    reference = _rdkit_dihedral(coords, 0, 1, 2, 3)
    assert abs(_periodic_diff(ours, reference)) < 1e-9


@pytest.mark.parametrize("phi_deg", [30.0, -30.0, 150.0, -150.0, 90.0, 123.4])
def test_full_reversal_preserves_angle(phi_deg: float) -> None:
    """Swapping the whole atom order (l,k,j,i) preserves the torsion value.

    Compared with a periodic difference so +180 and -180 are equivalent.
    """
    coords = _coords_for_dihedral(phi_deg)
    forward = GeometryUtils.calculate_dihedral(coords, 0, 1, 2, 3)
    reversed_ = GeometryUtils.calculate_dihedral(coords, 3, 2, 1, 0)
    assert abs(_periodic_diff(reversed_, forward)) < 1e-9


@pytest.mark.parametrize("phi_deg", [30.0, -30.0, 150.0, -150.0, 90.0, 123.4, 180.0])
def test_endpoint_swap_negates_angle(phi_deg: float) -> None:
    """Swapping the two endpoints while keeping the j-k axis (l,j,k,i) negates.

    Compared with a periodic difference so +180 and -180 are equivalent
    (negating 180 gives -180, which is the same angle).
    """
    coords = _coords_for_dihedral(phi_deg)
    forward = GeometryUtils.calculate_dihedral(coords, 0, 1, 2, 3)
    swapped = GeometryUtils.calculate_dihedral(coords, 3, 1, 2, 0)
    assert abs(_periodic_diff(swapped, -forward)) < 1e-9
