"""FCHL kernel availability tri-state, neighbour support and kernel budgets (plan todo 38; gap G12).

Combination matrix (CI / docs):

* **pure NumPy opt-in** (``ACP_FCHL_NUMPY=1``) — exercised in every run on the
  checked-in assets and deterministic training slices; the pure-numpy kernel is
  present in the codebase and only needs the opt-in, it is never "cannot run".
* **compiled qml** — exercised only when ``qml`` is genuinely importable; the
  parity test records ``NOT_VERIFIED`` (skipped, never counted as a pass)
  otherwise. CI installs neither qml nor a Fortran toolchain: qml builds
  against numpy<2 while ACP requires numpy>=2.1 (``pyproject.toml``; ``ci.yml``).
* **off** — neither qml importable nor the opt-in set: FCHL is inactive and DP5
  uses the unweighted KDE fallback. This is a configuration state, not an
  inability to run.

Out-of-domain handling: the FCHL-weighted KDE gets a neighbour-support metric
(contributing neighbours, importance-sampling effective count, support
fraction). An atom whose similarity vector has no contributing training
neighbour is typed ``out_of_domain`` and the candidate record withholds the
formal probability (``formal_probability is None``) while keeping the raw
fallback value for diagnostics.

Resource budget: kernel work is bounded by the requested slice (the 32-atom
training-slice convention from ``tests/test_acp_nmr_fchl_golden.py``); the full
53 208-atom numpy kernel (~25 min/query atom) is never invoked here.
"""

from __future__ import annotations

import logging
import sys
import types
from unittest.mock import patch

import numpy as np
import pytest

from acp.nmr.error_model import (
    DP5_CALIBRATION_OUT_OF_DOMAIN,
    DP5_CALIBRATION_STATUSES,
    DP5_CALIBRATION_UNWEIGHTED,
    DP5_CALIBRATION_WEIGHTED,
    DP5_PATHS,
    GoodmanDP5Model,
    dp5_fchl_available,
    dp5_model_available,
)
from acp.nmr.fchl import (
    ATOM_PROBABILITY_MODE_FALLBACK,
    ATOM_PROBABILITY_MODE_FCHL,
    DEFAULT_KERNEL_CHUNK_SIZE,
    KERNEL_CACHE_MAXSIZE,
    KERNEL_NUMPY_OPT_IN_ENV,
    KERNEL_STATE_NUMPY,
    KERNEL_STATE_OFF,
    KERNEL_STATE_QML,
    KERNEL_STATES,
    MIN_EFFECTIVE_NEIGHBORS,
    atom_probability_fchl_diagnostic,
    clear_kernel_cache,
    fchl_kernel_active,
    fchl_kernel_availability,
    get_atomic_kernels_numpy,
    kernel_backend,
    kernel_cache_info,
    kernel_similarity_support,
    load_atomic_reps,
    qml_kernel_available,
)

#: Training-slice convention from tests/test_acp_nmr_fchl_golden.py — the
#: budget test never touches the full 53 208-atom training set.
KERNEL_TRAIN_SLICE = 32
CHUNK_SIZE = 8

_DUMMY_REPS = np.zeros((1, 5, 86))


def _stub_qml_module() -> None:
    """Inject a minimal fake ``qml.fchl`` module (importability probe only)."""
    qml_mod = types.ModuleType("qml")
    qml_fchl = types.ModuleType("qml.fchl")
    qml_fchl.get_atomic_kernels = lambda *args, **kwargs: None  # probe never calls it
    qml_mod.fchl = qml_fchl
    sys.modules["qml"] = qml_mod
    sys.modules["qml.fchl"] = qml_fchl


@pytest.fixture(autouse=True)
def _isolate_qml_env_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start/end each test without stubbed ``qml``, without opt-in, with a cold cache."""
    for name in ("qml", "qml.fchl"):
        sys.modules.pop(name, None)
    monkeypatch.delenv(KERNEL_NUMPY_OPT_IN_ENV, raising=False)
    clear_kernel_cache()
    yield
    clear_kernel_cache()
    for name in ("qml", "qml.fchl"):
        sys.modules.pop(name, None)


@pytest.fixture()
def dp5_model() -> GoodmanDP5Model:
    if not dp5_model_available():
        pytest.skip("Goodman DP5 model files not present")
    return GoodmanDP5Model()


# ---------------------------------------------------------------------------
# Three explicit, distinguishable kernel states
# ---------------------------------------------------------------------------


def test_three_kernel_states_are_distinct_and_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert KERNEL_STATES == (KERNEL_STATE_QML, KERNEL_STATE_NUMPY, KERNEL_STATE_OFF)

    off = fchl_kernel_availability()
    assert off.state == KERNEL_STATE_OFF
    assert off.active is False
    assert off.backend == ""
    assert off.qml_importable is False
    assert off.numpy_opt_in is False
    assert kernel_backend() == ""
    assert fchl_kernel_active() is False
    assert dp5_fchl_available() is False

    monkeypatch.setenv(KERNEL_NUMPY_OPT_IN_ENV, "1")
    numpy_state = fchl_kernel_availability()
    assert numpy_state.state == KERNEL_STATE_NUMPY
    assert numpy_state.active is True
    assert numpy_state.backend == "numpy"
    assert numpy_state.numpy_opt_in is True
    assert kernel_backend() == "numpy"
    assert fchl_kernel_active() is True
    assert dp5_fchl_available() is True  # assets present + numpy opted in

    _stub_qml_module()
    qml_state = fchl_kernel_availability()
    assert qml_state.state == KERNEL_STATE_QML
    assert qml_state.backend == "qml"
    assert qml_state.qml_importable is True
    assert kernel_backend() == "qml"  # compiled kernel takes precedence over the opt-in

    assert len({off.state, numpy_state.state, qml_state.state}) == 3


def test_off_state_reason_names_the_opt_in_and_never_claims_fchl_cannot_run() -> None:
    availability = fchl_kernel_availability()
    assert availability.state == KERNEL_STATE_OFF
    assert KERNEL_NUMPY_OPT_IN_ENV in availability.reason
    reason = availability.reason.lower()
    assert "numpy" in reason
    assert "opt-in" in reason
    assert "present" in reason
    assert "cannot" not in reason


def test_environment_probe_matches_the_real_qml_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The expected branch derives from the real probe, never a hardcoded host fact."""
    assert "qml.fchl" not in sys.modules  # autouse isolation: no stub present
    qml_available = qml_kernel_available()
    assert fchl_kernel_availability().state == (
        KERNEL_STATE_QML if qml_available else KERNEL_STATE_OFF
    )

    monkeypatch.setenv(KERNEL_NUMPY_OPT_IN_ENV, "1")
    expected = KERNEL_STATE_QML if qml_available else KERNEL_STATE_NUMPY
    assert fchl_kernel_availability().state == expected
    assert kernel_backend() == expected


@pytest.mark.skipif(
    not qml_kernel_available(),
    reason=(
        "NOT_VERIFIED: compiled qml kernel not importable — the real-qml state was "
        "not exercised; skipped, never counted as a pass (qml needs numpy<2 + Fortran)"
    ),
)
def test_real_compiled_qml_reports_qml_state() -> None:
    availability = fchl_kernel_availability()
    assert availability.state == KERNEL_STATE_QML
    assert availability.qml_importable is True
    assert availability.active is True


# ---------------------------------------------------------------------------
# Neighbour support / out-of-domain
# ---------------------------------------------------------------------------


def test_support_metric_decreases_and_flags_out_of_domain() -> None:
    n_train = 32
    in_domain = kernel_similarity_support(np.ones(2 * n_train))
    partial = kernel_similarity_support(np.r_[np.ones(4), np.zeros(2 * n_train - 4)])
    out_of_domain = kernel_similarity_support(np.zeros(2 * n_train))

    assert in_domain.n_train == n_train
    assert in_domain.contributing_neighbors == n_train
    assert in_domain.effective_neighbors == pytest.approx(float(n_train))
    assert in_domain.support_fraction == pytest.approx(1.0)
    assert in_domain.out_of_domain is False

    assert partial.contributing_neighbors == 4
    assert partial.effective_neighbors == pytest.approx(4.0)
    assert partial.support_fraction == pytest.approx(4.0 / n_train)
    assert partial.out_of_domain is False

    assert out_of_domain.contributing_neighbors == 0
    assert out_of_domain.effective_neighbors == 0.0
    assert out_of_domain.support_fraction == 0.0
    assert out_of_domain.similarity_mass == 0.0
    assert out_of_domain.out_of_domain is True

    # support strictly decreases across the three samples, flag fires only when empty
    assert in_domain.support_fraction > partial.support_fraction > out_of_domain.support_fraction
    assert MIN_EFFECTIVE_NEIGHBORS == 1.0

    # explicit n_train override (un-doubled vectors)
    override = kernel_similarity_support(np.ones(4), n_train=4)
    assert override.effective_neighbors == pytest.approx(4.0)
    assert kernel_similarity_support(np.empty(0)).out_of_domain is True


def test_atom_diagnostic_flags_out_of_domain_weights(dp5_model: GoodmanDP5Model) -> None:
    folded = dp5_model.folded_errors
    weights = np.linspace(0.001, 1.0, folded.size)

    with patch("acp.nmr.fchl.atom_kernel_similarities", return_value=weights):
        record = atom_probability_fchl_diagnostic(
            _DUMMY_REPS[0], 1.5, folded, dp5_model.mean_abs_error, _DUMMY_REPS
        )
    assert record.mode == ATOM_PROBABILITY_MODE_FCHL
    assert record.out_of_domain is False
    assert record.support.contributing_neighbors > 0
    assert 0.0 <= record.probability <= 1.0

    with patch("acp.nmr.fchl.atom_kernel_similarities", return_value=np.zeros_like(weights)):
        fallback = atom_probability_fchl_diagnostic(
            _DUMMY_REPS[0], 1.5, folded, dp5_model.mean_abs_error, _DUMMY_REPS
        )
    assert fallback.mode == ATOM_PROBABILITY_MODE_FALLBACK
    assert fallback.out_of_domain is True
    assert fallback.support.effective_neighbors == 0.0
    # the raw unweighted KDE value is kept (diagnostics), but it is typed out-of-domain
    assert fallback.probability == pytest.approx(dp5_model.atom_probability(1.5), abs=1e-12)


def test_candidate_record_withholds_formal_probability_out_of_domain(
    dp5_model: GoodmanDP5Model,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KERNEL_NUMPY_OPT_IN_ENV, "1")
    folded = dp5_model.folded_errors
    weights = np.linspace(0.001, 1.0, folded.size)
    shifts = [[40.10, 30.20], [40.55, 29.75]]
    exp = [40.0, 30.0]
    boltz = [0.6, 0.4]
    reps = [[np.zeros((5, 86)), np.zeros((5, 86))], [np.zeros((5, 86)), np.zeros((5, 86))]]

    with patch("acp.nmr.fchl.atom_kernel_similarities", return_value=weights):
        weighted = dp5_model.probability_per_conformer_fchl_diagnostic(shifts, exp, boltz, reps)
    assert weighted.path == "fchl"
    assert weighted.calibration_status == DP5_CALIBRATION_WEIGHTED
    assert weighted.out_of_domain is False
    assert weighted.out_of_domain_atoms == 0
    assert weighted.formal_probability == pytest.approx(weighted.probability)
    with patch("acp.nmr.fchl.atom_kernel_similarities", return_value=weights):
        legacy = dp5_model.probability_per_conformer_fchl(shifts, exp, boltz, reps)
    assert legacy == pytest.approx(weighted.probability, abs=1e-12)

    with (
        patch("acp.nmr.fchl.atom_kernel_similarities", return_value=np.zeros_like(weights)),
        caplog.at_level(logging.WARNING, logger="acp.nmr.error_model"),
    ):
        out = dp5_model.probability_per_conformer_fchl_diagnostic(shifts, exp, boltz, reps)
    assert out.path == "fallback"
    assert out.out_of_domain is True
    assert out.out_of_domain_atoms == 2
    assert out.calibration_status == DP5_CALIBRATION_OUT_OF_DOMAIN
    assert out.formal_probability is None  # never a formal probability, never silent
    assert 0.0 <= out.probability <= 1.0  # raw fallback value kept for diagnostics
    assert "out of domain" in caplog.text.lower()


def test_weighted_and_unweighted_calibration_statuses_recorded_separately(
    dp5_model: GoodmanDP5Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KERNEL_NUMPY_OPT_IN_ENV, "1")
    shifts = [[40.10, 30.20]]
    exp = [40.0, 30.0]
    boltz = [1.0]

    unweighted = dp5_model.probability_per_conformer_diagnostic(shifts, exp, boltz)
    assert unweighted.path == "fallback"
    assert unweighted.calibration_status == DP5_CALIBRATION_UNWEIGHTED
    assert unweighted.out_of_domain is False
    assert unweighted.formal_probability == pytest.approx(unweighted.probability)

    weights = np.linspace(0.001, 1.0, dp5_model.folded_errors.size)
    reps = [[np.zeros((5, 86)), np.zeros((5, 86))]]
    with patch("acp.nmr.fchl.atom_kernel_similarities", return_value=weights):
        weighted = dp5_model.probability_per_conformer_fchl_diagnostic(shifts, exp, boltz, reps)
    assert weighted.path == "fchl"
    assert weighted.calibration_status == DP5_CALIBRATION_WEIGHTED
    assert weighted.formal_probability == pytest.approx(weighted.probability)

    assert DP5_CALIBRATION_WEIGHTED != DP5_CALIBRATION_UNWEIGHTED
    assert set(DP5_CALIBRATION_STATUSES) >= {
        DP5_CALIBRATION_WEIGHTED,
        DP5_CALIBRATION_UNWEIGHTED,
        DP5_CALIBRATION_OUT_OF_DOMAIN,
    }
    assert set(DP5_PATHS) == {"fchl", "fallback", "mixed"}


# ---------------------------------------------------------------------------
# Kernel chunking + bounded cache (resource budget)
# ---------------------------------------------------------------------------


def test_kernel_chunking_is_bit_identical_and_work_is_bounded() -> None:
    import acp.nmr.fchl as fchl_module

    atomic_reps = load_atomic_reps()[:KERNEL_TRAIN_SLICE]
    query = atomic_reps[:2]
    assert atomic_reps.shape[0] == KERNEL_TRAIN_SLICE

    clear_kernel_cache()
    full = get_atomic_kernels_numpy(query, atomic_reps, [0.025])
    clear_kernel_cache()

    calls = {"scalar": 0, "chunks": []}
    real_scalar = fchl_module._scalar_alchemy
    real_side = fchl_module._kernel_side_terms

    def counting_scalar(*args, **kwargs):
        calls["scalar"] += 1
        return real_scalar(*args, **kwargs)

    def spying_side(reps, nneigh, pmax, **kwargs):
        calls["chunks"].append(int(reps.shape[0]))
        return real_side(reps, nneigh, pmax, **kwargs)

    with (
        patch.object(fchl_module, "_scalar_alchemy", new=counting_scalar),
        patch.object(fchl_module, "_kernel_side_terms", new=spying_side),
    ):
        chunked = get_atomic_kernels_numpy(query, atomic_reps, [0.025], chunk_size=CHUNK_SIZE)

    assert np.array_equal(full, chunked)  # chunking is transparent, bit-identical
    n_a, n_b = query.shape[0], atomic_reps.shape[0]
    # bounded work: a-side self + b-side self + cross pairs, nothing more
    assert calls["scalar"] <= n_a + n_b + n_a * n_b
    b_chunks = [size for size in calls["chunks"] if size != n_a]
    assert b_chunks == [CHUNK_SIZE] * (n_b // CHUNK_SIZE)
    assert max(calls["chunks"]) <= CHUNK_SIZE
    # peak-memory bound is a module constant, never the full training set
    assert 0 < DEFAULT_KERNEL_CHUNK_SIZE <= 4096


def test_kernel_cache_is_bounded_and_reused() -> None:
    atomic_reps = load_atomic_reps()[:16]
    clear_kernel_cache()
    info_before = kernel_cache_info()
    assert info_before["size"] == 0
    assert info_before["maxsize"] == KERNEL_CACHE_MAXSIZE
    assert 0 < KERNEL_CACHE_MAXSIZE <= 16

    first = get_atomic_kernels_numpy(atomic_reps[:1], atomic_reps, [0.025])
    second = get_atomic_kernels_numpy(atomic_reps[:1], atomic_reps, [0.025])
    assert np.array_equal(first, second)
    assert kernel_cache_info()["hits"] >= 1

    # more distinct keys than maxsize -> eviction keeps the cache bounded
    for end in range(1, KERNEL_CACHE_MAXSIZE + 5):
        get_atomic_kernels_numpy(atomic_reps[:1], atomic_reps[:end], [0.025])
    final = kernel_cache_info()
    assert final["size"] == final["maxsize"] == KERNEL_CACHE_MAXSIZE

    clear_kernel_cache()
    assert kernel_cache_info()["size"] == 0
