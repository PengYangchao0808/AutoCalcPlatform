"""Tests for the FCHL-weighted DP5 path (DevDoc appendix D / P4).

Covers:

* the pure-numpy FCHL19 representation builder (shape + format parity with
  the qml reference, verified against the DP5 training-set layout);
* asset availability + qml detection;
* the FCHL-weighted atom probability and the ``sum(K_sim)==0`` fallback —
  exercised with a stubbed ``qml.fchl.get_atomic_kernels`` since the real
  ``qml`` package is a Fortran build unavailable on the head node;
* the runtime switch: ``dp5_mode`` is ``"fallback"`` when qml is absent
  and flips to ``"fchl"`` only when the FCHL path actually runs.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from acp.nmr.error_model import (
    GoodmanDP5Model,
    dp5_fchl_available,
    dp5_model_available,
)
from acp.nmr.fchl import (
    build_atom_representations,
    fchl_assets_available,
    generate_fchl_representation,
    get_atomic_kernels_numpy,
    load_atomic_reps,
    qml_kernel_available,
)


def _stub_qml_module() -> None:
    """Inject a minimal fake ``qml`` module with a configurable kernel.

    The FCHL representation builder is pure numpy (no qml needed), so only
    ``get_atomic_kernels`` must be faked. It maps every query atom to a
    similarity vector that is zero except for a target band of neighbours,
    so tests can exercise both the weighted path and the ``sum==0`` path.
    """
    from acp.nmr.fchl import atom_kernel_similarities  # noqa: F401

    qml_mod = types.ModuleType("qml")
    qml_fchl = types.ModuleType("qml.fchl")
    qml_fchl.get_atomic_kernels = _fake_get_atomic_kernels
    qml_mod.fchl = qml_fchl
    sys.modules["qml"] = qml_mod
    sys.modules["qml.fchl"] = qml_fchl


def _fake_get_atomic_kernels(a, b, sigmas, cut_distance=None, **kwargs):
    """Fake ``qml.fchl.get_atomic_kernels`` -> shape (nsigmas, nA, nB).

    Similarity between query atom A[0] and training atoms: 1.0 for the
    first ``n_sim`` neighbours, 0.0 otherwise. This lets tests set
    ``sum(K_sim)==0`` by requesting ``n_sim=0``.
    """
    n_sim = getattr(_fake_get_atomic_kernels, "n_sim", 50)
    n_a = a.shape[0]
    n_b = b.shape[0]
    n_sigmas = len(sigmas)
    out = np.zeros((n_sigmas, n_a, n_b))
    out[:, :, :n_sim] = 1.0
    return out


def _remove_qml() -> None:
    sys.modules.pop("qml", None)
    sys.modules.pop("qml.fchl", None)


@pytest.fixture(autouse=True)
def _isolate_qml_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts/ends with no stubbed qml and no env opt-in."""
    _remove_qml()
    monkeypatch.delenv("ACP_FCHL_NUMPY", raising=False)
    yield
    _remove_qml()


# ---------------------------------------------------------------------------
# Representation builder (pure numpy, no qml)
# ---------------------------------------------------------------------------


def test_generate_fchl_representation_shape_and_format() -> None:
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 0.9, 0.0]], float)
    zs = np.array([6, 1, 1])
    rep = generate_fchl_representation(coords, zs, max_size=86)
    # (max_size, 5, max_size) — the DP5 training-set layout (53208, 5, 86)
    assert rep.shape == (86, 5, 86)
    # row 0 = sorted neighbour distances, padding = 1e100
    d0 = rep[0, 0]
    finite = d0[d0 < 1e99]
    assert len(finite) == 3  # self + 2 neighbours within cut_distance
    assert finite[0] == pytest.approx(0.0)  # self distance
    assert np.all(np.diff(finite) >= 0)  # sorted
    # row 1 = nuclear charges of neighbours; rows 2-4 = displacement vectors
    assert set(rep[0, 1, :3]) <= {1, 6}
    assert rep[0, 2:5, :3].shape == (3, 3)


def test_build_atom_representations_returns_per_atom_descriptors() -> None:
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, 0.9, 0.0]], float)
    reps = build_atom_representations(coords, ["C", "H", "H"], [0])
    assert len(reps) == 1
    assert reps[0].shape == (5, 86)


def test_representation_matches_atomic_reps_feature_width() -> None:
    """Training atoms are (5, 86); our builder must emit the same width."""
    ar = load_atomic_reps()
    assert ar.ndim == 3
    assert ar.shape[1:] == (5, 86)
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], float)
    rep = build_atom_representations(coords, ["C", "H"], [0])[0]
    assert rep.shape == ar.shape[1:]


# ---------------------------------------------------------------------------
# Availability detection
# ---------------------------------------------------------------------------


def test_fchl_assets_available() -> None:
    assert fchl_assets_available() is True  # atomic_reps.gz + frag_reps.gz committed
    assert fchl_assets_available(Path("/nonexistent")) is False


def test_qml_kernel_available_false_without_qml(monkeypatch: pytest.MonkeyPatch) -> None:
    _remove_qml()
    monkeypatch.delenv("ACP_FCHL_NUMPY", raising=False)
    assert qml_kernel_available() is False
    # No qml and numpy backend not opted in → FCHL inactive → fallback
    assert dp5_fchl_available() is False
    from acp.nmr.fchl import kernel_backend

    assert kernel_backend() == ""


def test_numpy_backend_opt_in_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Robustness (P4): ACP_FCHL_NUMPY=1 activates the pure-numpy kernel."""
    _remove_qml()
    monkeypatch.setenv("ACP_FCHL_NUMPY", "1")
    from acp.nmr.fchl import kernel_backend

    assert kernel_backend() == "numpy"
    assert dp5_fchl_available() is True  # assets present + numpy opted in
    monkeypatch.delenv("ACP_FCHL_NUMPY", raising=False)


def test_dp5_fchl_available_requires_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    _remove_qml()
    monkeypatch.setenv("ACP_FCHL_NUMPY", "1")
    assert dp5_fchl_available() is True  # assets + numpy
    assert dp5_fchl_available(Path("/nonexistent")) is False  # no assets


# ---------------------------------------------------------------------------
# FCHL-weighted probability (stubbed qml kernel)
# ---------------------------------------------------------------------------


@pytest.fixture()
def dp5_model() -> GoodmanDP5Model:
    if not dp5_model_available():
        pytest.skip("Goodman DP5 model files not present")
    return GoodmanDP5Model()


def test_atom_probability_fchl_with_stub_qml(dp5_model: GoodmanDP5Model) -> None:
    _stub_qml_module()
    _fake_get_atomic_kernels.n_sim = 100  # similar neighbours exist
    reps = build_atom_representations(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], float), ["C", "H"], [0]
    )
    p_fchl = dp5_model.atom_probability_fchl(reps[0], 1.5)
    p_flat = dp5_model.atom_probability(1.5)
    assert 0.0 <= p_fchl <= 1.0
    # Weighted KDE on the same residual differs from the global KDE
    assert p_fchl != pytest.approx(p_flat, abs=1e-9)


def test_atom_probability_fchl_sum_zero_falls_back(dp5_model: GoodmanDP5Model) -> None:
    """sum(K_sim)==0 -> unweighted global KDE (DP5.py:98)."""
    _stub_qml_module()
    _fake_get_atomic_kernels.n_sim = 0  # no similar neighbours
    reps = build_atom_representations(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], float), ["C", "H"], [0]
    )
    p_fchl = dp5_model.atom_probability_fchl(reps[0], 2.0)
    p_flat = dp5_model.atom_probability(2.0)
    assert p_fchl == pytest.approx(p_flat, abs=1e-12)


def test_numpy_kernel_backend_runs_without_qml(
    dp5_model: GoodmanDP5Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Robustness (P4): the pure-numpy kernel computes the FCHL Gaussian
    similarity when opted in (ACP_FCHL_NUMPY=1). Verified directly on a small
    training subset rather than via atom_probability_fchl, which needs the
    full 106 416-residual / 53 208-atom pairing."""
    _remove_qml()
    monkeypatch.setenv("ACP_FCHL_NUMPY", "1")
    from acp.nmr.fchl import get_atomic_kernels_numpy, kernel_backend

    assert kernel_backend() == "numpy"
    ar = load_atomic_reps()
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], float)
    reps = build_atom_representations(coords, ["C", "H"], [0])
    # kernel of the training atoms against themselves — self-similarity == 1
    sub = ar[:4]
    k = get_atomic_kernels_numpy(sub[:1], sub, [0.025])
    assert k.shape == (1, 1, 4)
    assert k[0, 0, 0] == pytest.approx(1.0)  # self kernel
    assert np.all(k[0, 0, 1:] < 1.0)  # others are strictly less
    # query atom vs a few training atoms — finite, non-negative
    kq = get_atomic_kernels_numpy(np.array([reps[0]]), ar[:8], [0.025])
    assert kq.shape == (1, 1, 8)
    assert np.all(kq >= 0) and np.all(np.isfinite(kq))


def test_probability_per_conformer_fchl_requires_assets(
    dp5_model: GoodmanDP5Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    _remove_qml()
    monkeypatch.setenv("ACP_FCHL_NUMPY", "1")  # activate numpy backend
    assert dp5_model.fchl_available  # assets present + backend opted in
    # assets present → no RuntimeError; test invalid args instead
    with pytest.raises(ValueError, match="lengths differ"):
        dp5_model.probability_per_conformer_fchl([[1.0]], [1.0], [1.0], [])


def test_probability_per_conformer_fchl_sets_mode(dp5_model: GoodmanDP5Model) -> None:
    """The FCHL path flips dp5_mode to 'fchl'; the fallback path resets it."""
    _stub_qml_module()
    _fake_get_atomic_kernels.n_sim = 100
    reps = build_atom_representations(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], float), ["C", "H"], [0]
    )
    shifts = [[40.0], [40.2]]
    exp = [40.0]
    w = [0.5, 0.5]
    p = dp5_model.probability_per_conformer_fchl(shifts, exp, w, [[reps[0]], [reps[0]]])
    assert 0.0 <= p <= 1.0
    assert dp5_model.dp5_mode == "fchl"
    # fallback resets to "fallback"
    _ = dp5_model.probability_per_conformer(shifts, exp, w)
    assert dp5_model.dp5_mode == "fallback"


def test_fchl_and_fallback_differ_with_similar_neighbours(dp5_model: GoodmanDP5Model) -> None:
    """P4 acceptance: with similar neighbours present, FCHL != fallback."""
    _stub_qml_module()
    _fake_get_atomic_kernels.n_sim = 200
    reps = build_atom_representations(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], float), ["C", "H"], [0]
    )
    shifts = [[40.0, 30.0], [40.3, 29.7]]
    exp = [40.0, 30.0]
    w = [0.6, 0.4]
    p_fchl = dp5_model.probability_per_conformer_fchl(
        shifts, exp, w, [[reps[0], reps[0]], [reps[0], reps[0]]]
    )
    p_fallback = dp5_model.probability_per_conformer(shifts, exp, w)
    assert p_fchl != pytest.approx(p_fallback, abs=1e-9)


def test_load_atomic_reps_shape() -> None:
    ar = load_atomic_reps()
    assert ar.shape == (53208, 5, 86)


def test_periodic_distance_matrix_indexing() -> None:
    """Regression: pd must be indexed by charge-1 (Fortran 1-based via f2py).

    He(2)-Li(3) crosses a period boundary → alchemy distance is tiny (~0.008).
    An off-by-one (pd[charge] instead of pd[charge-1]) would read the Li-Be
    distance (~0.91) — a 100× error. This guards against that regression.
    """
    from acp.nmr.fchl import _periodic_distance, _periodic_distance_matrix

    pd = _periodic_distance_matrix()
    # Correct values from periodic_distance(charge, charge)
    assert _periodic_distance(2, 3, 1.6, 1.6) == pytest.approx(0.00758, abs=1e-4)
    assert _periodic_distance(6, 6, 1.6, 1.6) == pytest.approx(1.0)
    # pd[charge-1, charge-1] must equal periodic_distance(charge, charge)
    assert pd[1, 2] == pytest.approx(_periodic_distance(2, 3, 1.6, 1.6))
    assert pd[5, 5] == pytest.approx(_periodic_distance(6, 6, 1.6, 1.6))
    # He-Li must be tiny, not ~0.9 (the off-by-one regression value)
    assert pd[1, 2] < 0.01


def test_numpy_kernel_cross_charge_alchemy() -> None:
    """The kernel must distinguish same-charge from cross-charge neighbours.

    Two training atoms with {H,C,O} environments (rows 0,1) should be far
    more similar to each other than to an atom with a {H,C,N,O} environment
    (row 1000) — confirming the alchemy pd-matrix weighting is active and
    correct after the off-by-one fix.
    """
    ar = load_atomic_reps()
    triplet = ar[[0, 1, 1000]]
    k = get_atomic_kernels_numpy(triplet[:1], triplet, [0.025])
    # self-kernel exactly 1.0 (Gaussian property)
    assert k[0, 0, 0] == pytest.approx(1.0)
    # similar environment (atom1) >> dissimilar (atom1000)
    assert k[0, 0, 1] > k[0, 0, 2]


def test_atom_kernel_similarities_doubles_vector() -> None:
    """K_sim is hstack-doubled to match folded_scaled_errors (2x training atoms)."""
    _stub_qml_module()
    from acp.nmr.fchl import atom_kernel_similarities

    ar = load_atomic_reps()
    reps = build_atom_representations(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], float), ["C", "H"], [0]
    )
    k_sim = atom_kernel_similarities(reps[0], ar)
    assert k_sim.shape == (2 * ar.shape[0],)
    assert k_sim.shape == (106416,)
