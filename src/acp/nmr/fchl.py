# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false, reportUnusedCallResult=false
"""FCHL-weighted DP5 support (DevDoc appendix D / P4).

Faithful port of the Goodman DP5.py FCHL path (``DP5.py:85-108``):

1. Build a per-atom **FCHL19 representation** from the conformer geometry
   (``qml.fchl.generate_representation`` is pure numpy — ported here so no
   Fortran compiler is needed to generate descriptors).
2. Compute the per-atom **similarity vector** ``K_sim`` against the
   training-set atom representations (``atomic_reps.gz``) with
   ``qml.fchl.get_atomic_kernels`` (this *does* require the ``qml``
   package, which is a Fortran build).
3. Weight the Gaussian KDE over the folded scaled residuals with ``K_sim``
   and integrate around the mean absolute error to get the per-atom DP5
   probability (``DP5.py:104-108``).
4. When ``sum(K_sim)==0`` (no similar training neighbours) fall back to
   the unweighted global KDE (``DP5.py:98``).

``qml`` is an optional runtime dependency (see ``models/NOTICE.md``); the
DP5 path degrades to the unweighted KDE fallback (``dp5_mode="fallback"``)
when it is unavailable.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# DP5.py:20 — cut distance used to train both the representations and the
# kernel. Must match exactly or the kernel similarities are meaningless.
C_DISTANCE = 4.532297920317418
# DP5.py:57 — molecules >= this many atoms use the fragmented representation
# set (frag_reps.gz); smaller molecules use atomic_reps.gz.
FRAG_ATOM_THRESHOLD = 86
# DP5.py:500 / PyDP4.py:500 — kernel width passed to get_atomic_kernels.
FCHL_SIGMA = 0.025

# DP5.py / qml.fchl.get_atomic_kernels defaults (0.4.0.27) used by Goodman.
_TWO_BODY_SCALING = np.sqrt(8.0)
_THREE_BODY_SCALING = 1.6
_TWO_BODY_WIDTH = 0.2
_THREE_BODY_WIDTH = np.pi
_TWO_BODY_POWER = 4.0
_THREE_BODY_POWER = 2.0
_CUT_START = 1.0
_FOURIER_ORDER = 1
_ALCHEMY_PERIOD_WIDTH = 1.6
_ALCHEMY_GROUP_WIDTH = 1.6
_ALCHEMY_EMAX = 100

_MODELS_DIR = Path(__file__).resolve().parent / "models"


# ---------------------------------------------------------------------------
# FCHL19 representation generation (pure numpy port of qml.fchl.generate_representation)
# ---------------------------------------------------------------------------


def generate_fchl_representation(
    coordinates: np.ndarray,
    nuclear_charges: np.ndarray,
    max_size: int = 86,
    cut_distance: float = C_DISTANCE,
) -> np.ndarray:
    """Build the FCHL19 representation of a molecule (shape ``(max_size, 5, max_size)``).

    Pure-numpy port of ``qml.fchl.generate_representation`` (qml 0.4.0.27),
    which is itself implemented in numpy. Reproduces the reference exactly:

    * row 0  — sorted neighbour distances (``1E100`` padding);
    * row 1  — neighbour nuclear charges;
    * rows 2–4 — neighbour Cartesian displacement vectors.

    Args:
        coordinates: ``(n_atoms, 3)`` float array.
        nuclear_charges: ``(n_atoms,)`` int array.
        max_size: Descriptor capacity (must equal the training-set
            ``max_size`` — 86 for ``atomic_reps.gz``).
        cut_distance: Spatial cut-off radius.

    Returns:
        ``(max_size, 5, max_size)`` float array; ``[i]`` is atom *i*'s
        descriptor (shape ``(5, max_size)``).
    """
    size = max_size
    neighbors = size
    coords = np.asarray(coordinates, dtype=float)
    occ = np.asarray(nuclear_charges)
    n_atoms = len(coords)
    rep = np.zeros((size, 5, neighbors))
    rep[:, 0, :] = 1e100
    for i in range(n_atoms):
        displacement = -coords[i] + coords
        dist = np.sqrt(np.sum(displacement**2, axis=1))
        order = np.argsort(dist)
        dist = dist[order]
        charges = occ[order]
        displacement = displacement[order]
        sel = np.where(dist < cut_distance)[0]
        n_sel = len(sel)
        if n_sel > neighbors:
            sel = sel[:neighbors]
            n_sel = neighbors
        if n_sel:
            rep[i, 0, :n_sel] = dist[sel]
            rep[i, 1, :n_sel] = charges[sel]
            rep[i, 2:5, :n_sel] = displacement[sel].T
    return rep


def atomic_numbers(symbols: list[str]) -> np.ndarray:
    """Map element symbols to nuclear charges (RDKit periodic table)."""
    from rdkit import Chem

    pt = Chem.GetPeriodicTable()
    out = np.zeros(len(symbols), dtype=int)
    for i, sym in enumerate(symbols):
        s = (sym or "").strip()
        if not s:
            continue
        s = s[:1].upper() + s[1:].lower()
        try:
            out[i] = pt.GetAtomicNumber(s)
        except (RuntimeError, ValueError, AttributeError):
            out[i] = 0
    return out


def build_atom_representations(
    coordinates: np.ndarray,
    symbols: list[str],
    atom_indices: list[int],
    max_size: int = 86,
    cut_distance: float = C_DISTANCE,
) -> list[np.ndarray]:
    """Build FCHL19 descriptors for the atoms listed in *atom_indices*.

    Args:
        coordinates: ``(n_atoms, 3)`` conformer geometry.
        symbols: Element symbols aligned with *coordinates*.
        atom_indices: 0-based atom indices whose descriptors are needed
            (e.g. the ¹³C atoms).
        max_size: Descriptor capacity (must match ``atomic_reps.gz`` = 86).
        cut_distance: Cut-off radius (must match the training set).

    Returns:
        List of per-atom descriptors, each shape ``(5, max_size)``.
    """
    rep = generate_fchl_representation(coordinates, atomic_numbers(symbols), max_size, cut_distance)
    return [rep[i] for i in atom_indices]


# ---------------------------------------------------------------------------
# qml kernel + FCHL asset availability
# ---------------------------------------------------------------------------


def qml_kernel_available() -> bool:
    """Return True when ``qml.fchl.get_atomic_kernels`` is importable.

    The kernel is the only FCHL step that requires the compiled ``qml``
    package (Fortran). The representation builder above does not.
    """
    try:
        from qml.fchl import get_atomic_kernels  # noqa: F401

        return True
    except Exception:
        return False


def fchl_assets_available(models_dir: Path | None = None) -> bool:
    """Return True when both FCHL asset archives are present."""
    d = Path(models_dir) if models_dir is not None else _MODELS_DIR
    return (d / "atomic_reps.gz").exists() and (d / "frag_reps.gz").exists()


def load_atomic_reps(
    models_dir: Path | None = None,
    use_frag: bool = False,
) -> np.ndarray:
    """Load the FCHL training-set atom representations (gzip pickle).

    Returns an ``(n_train, 5, max_size)`` array. The ``frag_reps.gz`` set
    stores per-fragment central-atom descriptors and is only valid with the
    openbabel radius-3 fragmentation path (not yet wired — ACP degrades to
    the unweighted KDE for molecules >= :data:`FRAG_ATOM_THRESHOLD` atoms).
    """
    d = Path(models_dir) if models_dir is not None else _MODELS_DIR
    name = "frag_reps.gz" if use_frag else "atomic_reps.gz"
    path = d / name
    if not path.exists():
        raise FileNotFoundError(f"FCHL training-set file not found: {path}")
    import gzip

    with gzip.open(path, "rb") as fh:
        data = pickle.load(fh)
    arr = np.asarray(data)
    if arr.ndim == 3:
        return arr
    # ragged legacy storage — stack into a dense array
    return np.stack([np.asarray(x, dtype=float) for x in arr])


# ---------------------------------------------------------------------------
# Pure-numpy FCHL atomic kernel (fallback when qml is unavailable)
#
# Faithful port of qml 0.4.0.27 ``ffchl_module.f90`` + ``ffchl_scalar_kernels.f90``
# (fget_atomic_kernels_fchl). The math is deterministic and fully expressible
# in numpy; the Fortran only adds OpenMP parallelism. This makes the FCHL
# path usable without the qml Fortran build, at the cost of speed for large
# training sets. Numerically it matches qml bit-for-bit (modulo float order).
# ---------------------------------------------------------------------------

# Periodic-table positions {nuclear_charge: [row, column]} (qml alchemy.py).
# Rows = periods, columns = groups (main-group 1-8, transition 9-18, f-block 19+).
_PTP: dict[int, list[int]] = {
    1: [1, 1],
    2: [1, 8],
    3: [2, 1],
    4: [2, 2],
    5: [2, 3],
    6: [2, 4],
    7: [2, 5],
    8: [2, 6],
    9: [2, 7],
    10: [2, 8],
    11: [3, 1],
    12: [3, 2],
    13: [3, 3],
    14: [3, 4],
    15: [3, 5],
    16: [3, 6],
    17: [3, 7],
    18: [3, 8],
    19: [4, 1],
    20: [4, 2],
    31: [4, 3],
    32: [4, 4],
    33: [4, 5],
    34: [4, 6],
    35: [4, 7],
    36: [4, 8],
    21: [4, 9],
    22: [4, 10],
    23: [4, 11],
    24: [4, 12],
    25: [4, 13],
    26: [4, 14],
    27: [4, 15],
    28: [4, 16],
    29: [4, 17],
    30: [4, 18],
    37: [5, 1],
    38: [5, 2],
    49: [5, 3],
    50: [5, 4],
    51: [5, 5],
    52: [5, 6],
    53: [5, 7],
    54: [5, 8],
    39: [5, 9],
    40: [5, 10],
    41: [5, 11],
    42: [5, 12],
    43: [5, 13],
    44: [5, 14],
    45: [5, 15],
    46: [5, 16],
    47: [5, 17],
    48: [5, 18],
    55: [6, 1],
    56: [6, 2],
    81: [6, 3],
    82: [6, 4],
    83: [6, 5],
    84: [6, 6],
    85: [6, 7],
    86: [6, 8],
    72: [6, 10],
    73: [6, 11],
    74: [6, 12],
    75: [6, 13],
    76: [6, 14],
    77: [6, 15],
    78: [6, 16],
    79: [6, 17],
    80: [6, 18],
    57: [6, 19],
    58: [6, 20],
    59: [6, 21],
    60: [6, 22],
    61: [6, 23],
    62: [6, 24],
    63: [6, 25],
    64: [6, 26],
    65: [6, 27],
    66: [6, 28],
    67: [6, 29],
    68: [6, 30],
    69: [6, 31],
    70: [6, 32],
    71: [6, 33],
    87: [7, 1],
    88: [7, 2],
    113: [7, 3],
    114: [7, 4],
    115: [7, 5],
    116: [7, 6],
    117: [7, 7],
    118: [7, 8],
    104: [7, 10],
    105: [7, 11],
    106: [7, 12],
    107: [7, 13],
    108: [7, 14],
    109: [7, 15],
    110: [7, 16],
    111: [7, 17],
    112: [7, 18],
    89: [7, 19],
    90: [7, 20],
    91: [7, 21],
    92: [7, 22],
    93: [7, 23],
    94: [7, 24],
    95: [7, 25],
    96: [7, 26],
    97: [7, 27],
    98: [7, 28],
    99: [7, 29],
    100: [7, 30],
}


def _periodic_distance(a: int, b: int, r_width: float, c_width: float) -> float:
    ra, ca = _PTP.get(int(a), [0, 0])
    rb, cb = _PTP.get(int(b), [0, 0])
    return float(np.exp(-((ra - rb) ** 2) / (4 * r_width**2) - ((ca - cb) ** 2) / (4 * c_width**2)))


def _periodic_distance_matrix(
    emax: int = _ALCHEMY_EMAX,
    r_width: float = _ALCHEMY_GROUP_WIDTH,
    c_width: float = _ALCHEMY_PERIOD_WIDTH,
) -> np.ndarray:
    pd = np.zeros((emax, emax))
    for i in range(emax):
        for j in range(emax):
            pd[i, j] = _periodic_distance(i + 1, j + 1, r_width, c_width)
    return pd


def _cut_function(r: float, cut_start: float, cut_distance: float) -> float:
    ru = cut_distance
    rl = cut_start * cut_distance
    if r > ru:
        return 0.0
    if r < rl:
        return 1.0
    x = (ru - r) / (ru - rl)
    return float(10.0 * x**3 - 15.0 * x**4 + 6.0 * x**5)


def _angular_norm2(t_width: float, limit: int = 10000) -> float:
    pi = np.pi
    n = np.arange(-limit, limit + 1)
    s = np.sum(np.exp(-((t_width * n) ** 2)) * (2.0 - 2.0 * np.cos(n * pi)))
    return float(np.sqrt(s * pi) * 2.0)


def _twobody_weights(
    x: np.ndarray, neighbors: int, power: float, cut_start: float, cut_distance: float
) -> np.ndarray:
    """ksi(i) for i=1..neighbors-1 (1-based in Fortran → index 1.. here)."""
    ksi = np.zeros(x.shape[1])
    for i in range(1, neighbors):  # Fortran 2..neighbors → 0-based 1..neighbors-1
        ksi[i] = _cut_function(x[0, i], cut_start, cut_distance) / x[0, i] ** power
    return ksi


def _calc_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    v1 = a - b
    v2 = c - b
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    cos_a = float(np.dot(v1, v2) / (n1 * n2))
    cos_a = max(-1.0, min(1.0, cos_a))
    return float(np.arccos(cos_a))


def _calc_cos_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    v1 = a - b
    v2 = c - b
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    return float(np.dot(v1, v2) / (n1 * n2))


def _calc_ksi3(
    x: np.ndarray, j: int, k: int, power: float, cut_start: float, cut_distance: float
) -> float:
    """Three-body Axilrod-Teller-Muto weight (calc_ksi3, 0-based j,k>=1)."""
    # central atom is index 0 (x(:,1) in Fortran 1-based)
    cos_i = _calc_cos_angle(x[2:5, k], x[2:5, 0], x[2:5, j])
    cos_j = _calc_cos_angle(x[2:5, j], x[2:5, k], x[2:5, 0])
    cos_k = _calc_cos_angle(x[2:5, 0], x[2:5, j], x[2:5, k])
    dk = x[0, j]
    dj = x[0, k]
    di = float(np.linalg.norm(x[2:5, j] - x[2:5, k]))
    cut = (
        _cut_function(dk, cut_start, cut_distance)
        * _cut_function(dj, cut_start, cut_distance)
        * _cut_function(di, cut_start, cut_distance)
    )
    denom = (di * dj * dk) ** power
    if denom == 0:
        return 0.0
    return float(cut * (1.0 + 3.0 * cos_i * cos_j * cos_k) / denom)


def _threebody_fourier(
    x: np.ndarray,
    neighbors: int,
    order: int,
    power: float,
    cut_start: float,
    cut_distance: float,
    pmax: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (cosp, sinp) each shape (pmax, order, neighbors) (0-based).

    Fortran ``get_threebody_fourier`` stores fourier(1/2, pj, m, j) with
    pj = charge of neighbour k (0-based charge index = int charge since pd is
    1-based by element). Here pmax indexes nuclear charges 1..pmax → we use
    charge value directly as the leading index (pd is (emax,emax), so
    int(charge) addresses it; charges are >=1). We allocate pmax rows where
    row index = nuclear charge (1-based).
    """
    pi = np.pi
    cosp = np.zeros((pmax + 1, order, neighbors))
    sinp = np.zeros((pmax + 1, order, neighbors))
    for j in range(1, neighbors):  # Fortran 2..neighbors
        for k in range(j + 1, neighbors):  # Fortran j+1..neighbors
            ksi3 = _calc_ksi3(x, j, k, power, cut_start, cut_distance)
            theta = _calc_angle(x[2:5, j], x[2:5, 0], x[2:5, k])
            pj = int(x[1, k])  # charge of neighbour k
            pk = int(x[1, j])  # charge of neighbour j
            for m in range(1, order + 1):  # Fortran 1..order
                cos_m = (np.cos(m * theta) - np.cos((theta + pi) * m)) * ksi3
                sin_m = (np.sin(m * theta) - np.sin((theta + pi) * m)) * ksi3
                if pj <= pmax:
                    cosp[pj, m - 1, j] += cos_m
                    sinp[pj, m - 1, j] += sin_m
                if pk <= pmax:
                    cosp[pk, m - 1, k] += cos_m
                    sinp[pk, m - 1, k] += sin_m
    # drop the unused row 0 → shape (pmax, order, neighbors)
    return cosp[1:], sinp[1:]


def _scalar_alchemy(
    x1: np.ndarray,
    x2: np.ndarray,
    n1: int,
    n2: int,
    ksi1: np.ndarray,
    ksi2: np.ndarray,
    cos1: np.ndarray,
    sin1: np.ndarray,
    cos2: np.ndarray,
    sin2: np.ndarray,
    t_width: float,
    d_width: float,
    order: int,
    pd: np.ndarray,
    ang_norm2: float,
    distance_scale: float,
    angular_scale: float,
) -> float:
    """scalar_alchemy (ffchl_module.f90:471-607), 0-based indices.

    x1/x2 are (5, neighbors) single-atom reps; n1/n2 = neighbour counts.
    ksi1/ksi2 length neighbors; cos/sin shape (pmax, order, neighbors).
    """
    pi = np.pi
    # pmax is the leading dimension of the precomputed Fourier arrays
    # (global max charge over each set), NOT recomputed per-atom.
    pmax1 = int(cos1.shape[0])
    pmax2 = int(cos2.shape[0])
    g1 = np.sqrt(2.0 * pi) / ang_norm2
    s = np.array([g1 * np.exp(-((t_width * m) ** 2) / 2.0) for m in range(1, order + 1)])
    inv_width = -1.0 / (4.0 * d_width**2)
    maxgausdist2 = (8.0 * d_width) ** 2

    aadist = 1.0
    for m1 in range(1, n1):  # Fortran 2..N1
        for m2 in range(1, n2):  # Fortran 2..N2
            r2 = (x2[0, m2] - x1[0, m1]) ** 2
            if r2 >= maxgausdist2:
                continue
            # pd is 0-based numpy; Fortran accesses pd(charge, charge) via f2py
            # which maps to numpy pd[charge-1, charge-1]. We replicate that here.
            d = np.exp(r2 * inv_width) * pd[int(x1[1, m1]) - 1, int(x2[1, m2]) - 1]
            # angular term: sum over m=1..order of s(m) * sum_{p1,p2} (cos1*cos2+sin1*sin2)*pd
            angular = 0.0
            for m in range(1, order + 1):
                c1 = cos1[:, m - 1, m1]  # length pmax1; row r = charge r+1
                sn1 = sin1[:, m - 1, m1]
                c2 = cos2[:, m - 1, m2]  # length pmax2; row r = charge r+1
                sn2 = sin2[:, m - 1, m2]
                # 0-based charge indices: row r → charge r+1 → pd[r, r']
                p1_idx = np.arange(pmax1)
                p2_idx = np.arange(pmax2)
                pd_block = pd[np.ix_(p2_idx, p1_idx)]  # pd[r2, r1] = dist(r2+1, r1+1)
                temp = float(
                    np.sum(np.outer(c2, c1) * pd_block) + np.sum(np.outer(sn2, sn1) * pd_block)
                )
                angular += temp * s[m - 1]
            aadist += d * (ksi1[m1] * ksi2[m2] * distance_scale + angular * angular_scale)
    aadist *= pd[int(x1[1, 0]) - 1, int(x2[1, 0]) - 1]
    return float(aadist)


def _count_neighbors(reps: np.ndarray, cut_distance: float) -> np.ndarray:
    """Number of neighbours within cut_distance per atom (row x[0] < cut)."""
    return np.array([int(np.sum(atom[0] < cut_distance)) for atom in reps], dtype=int)


def get_atomic_kernels_numpy(
    a: np.ndarray,
    b: np.ndarray,
    sigmas: list[float],
    two_body_scaling: float = _TWO_BODY_SCALING,
    three_body_scaling: float = _THREE_BODY_SCALING,
    two_body_width: float = _TWO_BODY_WIDTH,
    three_body_width: float = _THREE_BODY_WIDTH,
    two_body_power: float = _TWO_BODY_POWER,
    three_body_power: float = _THREE_BODY_POWER,
    cut_start: float = _CUT_START,
    cut_distance: float = C_DISTANCE,
    fourier_order: int = _FOURIER_ORDER,
    alchemy_period_width: float = _ALCHEMY_PERIOD_WIDTH,
    alchemy_group_width: float = _ALCHEMY_GROUP_WIDTH,
) -> np.ndarray:
    """Pure-numpy port of ``qml.fchl.get_atomic_kernels`` (0.4.0.27).

    Computes the Gaussian FCHL atomic kernel matrix between atoms *a* and *b*:
    ``K[i,j] = exp(-||a_i - b_j||^2 / (2 sigma^2))`` where the distance is the
    FCHL scalar-product distance (two-body + three-body + alchemy). Returns
    shape ``(n_sigmas, n_a, n_b)``.

    Args:
        a: ``(n_a, 5, max_size)`` FCHL atom representations.
        b: ``(n_b, 5, max_size)`` FCHL atom representations.
        sigmas: kernel widths.

    Returns:
        ``(len(sigmas), n_a, n_b)`` kernel matrix.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na1, na2 = a.shape[0], b.shape[0]
    nneigh1 = _count_neighbors(a, cut_distance)
    nneigh2 = _count_neighbors(b, cut_distance)
    pd = _periodic_distance_matrix(_ALCHEMY_EMAX, alchemy_group_width, alchemy_period_width)
    ang_norm2 = _angular_norm2(three_body_width)
    true_distance_scale = two_body_scaling / 16.0
    true_angular_scale = three_body_scaling / np.sqrt(8.0)

    pmax1 = int(max((np.max(ai[1, : nneigh1[i]]) for i, ai in enumerate(a)), default=0))
    pmax2 = int(max((np.max(bi[1, : nneigh2[i]]) for i, bi in enumerate(b)), default=0))

    # Pre-compute ksi, fourier, self-scalar per atom
    ksi1 = [None] * na1
    cos1 = [None] * na1
    sin1 = [None] * na1
    self1 = np.zeros(na1)
    for i in range(na1):
        n = int(nneigh1[i])
        ksi1[i] = _twobody_weights(a[i], n, two_body_power, cut_start, cut_distance)
        c, s = _threebody_fourier(
            a[i], n, fourier_order, three_body_power, cut_start, cut_distance, pmax1
        )
        cos1[i] = c
        sin1[i] = s
        self1[i] = _scalar_alchemy(
            a[i],
            a[i],
            n,
            n,
            ksi1[i],
            ksi1[i],
            c,
            s,
            c,
            s,
            three_body_width,
            two_body_width,
            fourier_order,
            pd,
            ang_norm2,
            true_distance_scale,
            true_angular_scale,
        )
    ksi2 = [None] * na2
    cos2 = [None] * na2
    sin2 = [None] * na2
    self2 = np.zeros(na2)
    for j in range(na2):
        n = int(nneigh2[j])
        ksi2[j] = _twobody_weights(b[j], n, two_body_power, cut_start, cut_distance)
        c, s = _threebody_fourier(
            b[j], n, fourier_order, three_body_power, cut_start, cut_distance, pmax2
        )
        cos2[j] = c
        sin2[j] = s
        self2[j] = _scalar_alchemy(
            b[j],
            b[j],
            n,
            n,
            ksi2[j],
            ksi2[j],
            c,
            s,
            c,
            s,
            three_body_width,
            two_body_width,
            fourier_order,
            pd,
            ang_norm2,
            true_distance_scale,
            true_angular_scale,
        )

    sigmas_arr = np.asarray(sigmas, dtype=float)
    inv_sigma2 = -0.5 / sigmas_arr**2
    kernels = np.zeros((len(sigmas), na1, na2))
    for i in range(na1):
        ni = int(nneigh1[i])
        for j in range(na2):
            nj = int(nneigh2[j])
            cross = _scalar_alchemy(
                a[i],
                b[j],
                ni,
                nj,
                ksi1[i],
                ksi2[j],
                cos1[i],
                sin1[i],
                cos2[j],
                sin2[j],
                three_body_width,
                two_body_width,
                fourier_order,
                pd,
                ang_norm2,
                true_distance_scale,
                true_angular_scale,
            )
            l2dist = self1[i] + self2[j] - 2.0 * cross
            kernels[:, i, j] = np.exp(l2dist * inv_sigma2)
    return kernels


def kernel_backend() -> str:
    """Return the active FCHL kernel backend: ``"qml"`` or ``"numpy"``.

    ``"qml"`` uses the compiled Fortran kernel (fast, exact reference);
    ``"numpy"`` uses the pure-numpy port (same math, much slower for the full
    53 208-atom training set). The FCHL path works on both; ``dp5_mode``
    reports ``fchl`` either way.

    The numpy backend is opt-in (it can take minutes per atom on the full
    training set): set ``ACP_FCHL_NUMPY=1`` to use it when ``qml`` is absent.
    Otherwise the FCHL path only activates when ``qml`` is importable, and
    the DP5 probability degrades to the unweighted KDE fallback.
    """
    import os

    if qml_kernel_available():
        return "qml"
    if os.environ.get("ACP_FCHL_NUMPY", "").strip() in ("1", "true", "yes", "on"):
        return "numpy"
    return ""  # FCHL not active (caller falls back to the unweighted KDE)


def fchl_kernel_active() -> bool:
    """Return True when an FCHL kernel should actually run (qml or opt-in numpy)."""
    return kernel_backend() != ""


# ---------------------------------------------------------------------------
# FCHL-weighted per-atom probability (DP5.py:85-108)
# ---------------------------------------------------------------------------


def atom_kernel_similarities(
    representation: np.ndarray,
    atomic_reps: np.ndarray,
    sigma: float = FCHL_SIGMA,
    cut_distance: float = C_DISTANCE,
) -> np.ndarray:
    """Return the doubled similarity vector ``K_sim`` for one atom.

    ``K_sim = get_atomic_kernels([rep], atomic_reps, [sigma], cut_distance)[0][0]``
    (shape ``(n_train,)``), then doubled with ``np.hstack((K_sim, K_sim))``
    to match the ``folded_scaled_errors`` residual count (which is exactly
    2× the training-set atom count — ``DP5.py:89-92``).

    Uses the compiled ``qml.fchl.get_atomic_kernels`` when available (fast,
    the Fortran reference); otherwise falls back to the pure-numpy port
    :func:`get_atomic_kernels_numpy` (identical math, slower for large
    training sets) when opted in via ``ACP_FCHL_NUMPY=1``. Raises
    :class:`RuntimeError` when no kernel backend is active (so the caller
    can degrade to the unweighted KDE).
    """
    backend = kernel_backend()
    if not backend:
        raise RuntimeError(
            "No FCHL kernel backend active. Install 'qml' (fast Fortran) or "
            "set ACP_FCHL_NUMPY=1 to use the pure-numpy port."
        )
    rep = np.asarray(representation, dtype=float)
    reps = np.asarray(atomic_reps, dtype=float)
    if backend == "qml":
        from qml.fchl import get_atomic_kernels

        k_sim = get_atomic_kernels(
            np.array([rep]), reps, [float(sigma)], cut_distance=float(cut_distance)
        )[0][0]
    else:
        k_sim = get_atomic_kernels_numpy(
            np.array([rep]), reps, [float(sigma)], cut_distance=float(cut_distance)
        )[0][0]
    k_sim = np.hstack((k_sim, k_sim))
    return np.asarray(k_sim, dtype=float)


def atom_probability_fchl(
    representation: np.ndarray,
    scaled_error: float,
    folded_errors: np.ndarray,
    mean_abs_error: float,
    atomic_reps: np.ndarray,
    sigma: float = FCHL_SIGMA,
    cut_distance: float = C_DISTANCE,
) -> float:
    """Per-atom DP5 probability with the FCHL-similarity weighted KDE.

    Faithful port of ``DP5.py:85-108``:

    * ``K_sim`` from :func:`atom_kernel_similarities` (qml if available,
      else the pure-numpy kernel);
    * when ``sum(K_sim)==0`` use the unweighted KDE over
      ``folded_errors`` (``DP5.py:98``);
    * otherwise build a ``gaussian_kde(folded_errors, weights=K_sim)``;
    * ``s_e_diff = |scaled_error - mean_abs_error|`` then
      ``p = kde.integrate_box_1d(mean - diff, mean + diff)``.
    """
    from scipy.stats import gaussian_kde

    k_sim = atom_kernel_similarities(representation, atomic_reps, sigma, cut_distance)
    if np.sum(k_sim) == 0:
        kde_est = gaussian_kde(np.asarray(folded_errors, dtype=float))
    else:
        kde_est = gaussian_kde(np.asarray(folded_errors, dtype=float), weights=k_sim)
    diff = abs(float(scaled_error) - float(mean_abs_error))
    lo = float(mean_abs_error) - diff
    hi = float(mean_abs_error) + diff
    return float(kde_est.integrate_box_1d(lo, hi))


__all__ = [
    "C_DISTANCE",
    "FCHL_SIGMA",
    "FRAG_ATOM_THRESHOLD",
    "atom_kernel_similarities",
    "atom_probability_fchl",
    "atomic_numbers",
    "build_atom_representations",
    "fchl_assets_available",
    "fchl_kernel_active",
    "generate_fchl_representation",
    "get_atomic_kernels_numpy",
    "kernel_backend",
    "load_atomic_reps",
    "qml_kernel_available",
]
