# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Error models for DP4/DP5 (DevDoc §10).

Verified against the Goodman-lab/DP5 source (``DP4.py`` / ``DP5.py``,
fetched 2026-08-07):

* **DP4** uses a **Gaussian** distribution (not Student-t as the DevDoc
  draft §8.5 speculated) with σ_C = 2.269372, σ_H = 0.187311 ppm
  (``DP4.py:17-21``, probability ``2·Φ(-|r/σ|)``).
* **DP5** uses a Gaussian KDE over folded scaled residuals
  (``folded_scaled_errors``, 106 416 points) for per-atom probabilities,
  then a Bayesian rescale via two weighted KDEs of per-candidate scores
  (``c_w_kde`` = correct-assignment, ``i_w_kde`` = incorrect-assignment).

The trained KDE/KDE-rescale assets live in ``acp/nmr/models/`` and are
rebuilt at load time from their stored ``dataset``+``weights``+``factor``
(scipy's internal API changed; we do not rely on the pickled methods).
"""

from __future__ import annotations

import logging
import math
import pickle
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from acp.nmr.models import NmrConfig

logger = logging.getLogger(__name__)

_MODELS_DIR = Path(__file__).resolve().parent / "models"


# ---------------------------------------------------------------------------
# Reference-level binding (DevDoc §10.2)
# ---------------------------------------------------------------------------


_GOODMAN_LEVEL = ("mPW1PW91", "6-311G(d)", "goodman-legacy")


def validate_error_model_binding(config: NmrConfig) -> None:
    """Raise ``ValueError`` when the error model and NMR level diverge.

    DevDoc §10.2: the Goodman distributions are trained on
    ``mPW1PW91/6-311G(d)``. Using them with a different level produces
    meaningless probabilities.
    """
    method_ok = config.nmr_method.strip().lower() == _GOODMAN_LEVEL[0].lower()
    basis_ok = _basis_equal(config.nmr_basis, _GOODMAN_LEVEL[1])
    model_ok = config.error_model.strip().lower() == _GOODMAN_LEVEL[2].lower()
    placeholder = config.error_model.strip().lower().startswith("placeholder")

    if placeholder:
        logger.warning(
            "Using placeholder error model '%s' — DP4/DP5 values are "
            "relative only, not for publication.",
            config.error_model,
        )
        return

    if model_ok and not (method_ok and basis_ok):
        raise ValueError(
            f"Error model '{config.error_model}' requires NMR level "
            f"mPW1PW91/6-311G(d) but got {config.nmr_method}/{config.nmr_basis}. "
            "Switch the error model (and its trained parameters) to match."
        )


def _basis_equal(actual: str, expected: str) -> bool:
    a = actual.strip().lower().replace(" ", "")
    b = expected.strip().lower().replace(" ", "")
    return a == b


# ---------------------------------------------------------------------------
# DP4 ErrorModel abstraction
# ---------------------------------------------------------------------------


class ErrorModel(ABC):
    """Abstract DP4 error distribution for residual → likelihood conversion."""

    model_id: str = "abstract"

    @abstractmethod
    def log_likelihood(self, residuals: list[float], nucleus: str) -> float:
        """Return ``ln Π_i f(r_i)`` for *residuals* of one nucleus."""
        raise NotImplementedError

    def likelihood(self, residuals: list[float], nucleus: str) -> float:
        """Return ``exp(log_likelihood)`` (may underflow to 0)."""
        return math.exp(self.log_likelihood(residuals, nucleus))


class PlaceholderStudentTErrorModel(ErrorModel):
    """Student-t error model with literature-approximate parameters.

    **Deprecated** since P1b — kept for backward-compat / fallback only.
    The real Goodman DP4 uses a Gaussian (:class:`GoodmanErrorModel`).
    """

    model_id = "placeholder-student-t"

    def __init__(
        self,
        sigma: dict[str, float] | None = None,
        nu: float = 4.0,
    ) -> None:
        self.sigma = sigma or {"1H": 0.12, "13C": 2.2}
        self.nu = float(nu)

    def log_likelihood(self, residuals: list[float], nucleus: str) -> float:
        if not residuals:
            return 0.0
        sigma = self.sigma.get(nucleus)
        if sigma is None or sigma <= 0:
            return 0.0
        nu = self.nu
        log_const = (
            math.lgamma((nu + 1) / 2)
            - math.lgamma(nu / 2)
            - 0.5 * math.log(nu * math.pi)
            - math.log(sigma)
        )
        total = 0.0
        for r in residuals:
            z = float(r) / sigma
            total += log_const - 0.5 * (nu + 1) * math.log1p(z * z / nu)
        return total


class GoodmanErrorModel(ErrorModel):
    """Goodman DP4 Gaussian error model (verified DP4.py:17-21, 190-194).

    Per-residual probability ``P = 2·Φ(-|r/σ|)`` (two-tailed Gaussian),
    σ_C = 2.269372, σ_H = 0.187311 ppm, mean = 0. The likelihood is the
    product of per-residual probabilities; ``log_likelihood`` returns the
    sum of logs for numerical stability.
    """

    model_id = "goodman-legacy"

    # Verified against DP4.py:17-21
    SIGMA = {"13C": 2.269372270818724, "1H": 0.18731058105269952}
    MEAN = 0.0

    def log_likelihood(self, residuals: list[float], nucleus: str) -> float:
        if not residuals:
            return 0.0
        sigma = self.SIGMA.get(nucleus)
        if sigma is None or sigma <= 0:
            logger.debug("No σ for nucleus %s in Goodman model; skipping", nucleus)
            return 0.0
        # P(r) = 2 * Φ(-|r/σ|); log P = log(2) + log Φ(-|z|)
        # Φ(-|z|) = 0.5 * erfc(|z| / sqrt(2)); log(2·0.5·erfc) = log(erfc(...))
        total = 0.0
        for r in residuals:
            z = abs(float(r) / sigma)
            # erfc via math (stable for moderate z; for large z, log-erfc → -inf)
            log_p = math.log(math.erfc(z / math.sqrt(2.0)))
            total += log_p
        return total


# ---------------------------------------------------------------------------
# DP5 model (KDE-based, Goodman DP5.py)
# ---------------------------------------------------------------------------


def _rebuild_kde(pickle_path: Path):
    """Rebuild a fresh scipy ``gaussian_kde`` from a legacy pickle.

    The Goodman pickles were created with an older scipy whose
    ``gaussian_kde`` lacks the ``cho_cov`` attribute the current version
    expects. We extract ``dataset``+``weights``+``factor`` and build a
    new KDE instead of trusting the pickled methods.
    """
    import warnings

    from scipy.stats import gaussian_kde

    # The legacy gaussian_kde pickle emits a DeprecationWarning on unpickle
    # (scipy.stats.kde namespace rename); suppress it for both load + rebuild.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with pickle_path.open("rb") as fh:
            old = pickle.load(fh)
        dataset = np.asarray(old.dataset)  # shape (d, n)
        weights = np.asarray(old.weights).flatten()
        return gaussian_kde(dataset, weights=weights, bw_method=old.factor)


class GoodmanDP5Model:
    """Goodman DP5 probability model (verified DP5.py:73-141, 356-383).

    Two per-atom probability paths:

    * **FCHL-weighted** (``DP5.py:85-108``) — per-atom FCHL19
      representations built from the conformer geometry are kernel-compared
      against the training-set atoms (``atomic_reps.gz``) with ``qml``, and
      the resulting similarity vector weights the Gaussian KDE over the
      folded scaled residuals. This is Goodman's full DP5 innovation and is
      active when ``qml`` is importable and the FCHL assets are present
      (:attr:`fchl_available`).
    * **Unweighted fallback** (``DP5.py:98``) — the global KDE over
      ``folded_scaled_errors``, used when ``qml``/assets are unavailable or
      when an atom has no similar training neighbours (``sum(K_sim)==0``).

    Both share the same downstream ``Rescale_DP5`` (DP5.py:367-383) via the
    correct/incorrect KDEs. :attr:`dp5_mode` reports which path ran.
    """

    model_id = "goodman-dp5"

    def __init__(self, models_dir: Path | None = None) -> None:
        models_dir = models_dir or _MODELS_DIR
        fse_path = models_dir / "folded_scaled_errors.p"
        c_path = models_dir / "c_w_kde_mean_s_0.025.p"
        i_path = models_dir / "i_w_kde_mean_s_0.025.p"
        if not all(p.exists() for p in (fse_path, c_path, i_path)):
            raise FileNotFoundError(
                f"Goodman DP5 model files missing in {models_dir}; expected "
                "folded_scaled_errors.p, c_w_kde_mean_s_0.025.p, "
                "i_w_kde_mean_s_0.025.p"
            )
        with fse_path.open("rb") as fh:
            self.folded_errors = np.asarray(pickle.load(fh))
        self.mean_abs_error = float(np.mean(np.abs(self.folded_errors)))
        self._correct_kde = _rebuild_kde(c_path)
        self._incorrect_kde = _rebuild_kde(i_path)
        self._atom_kde = None  # lazy — built on first use
        self._atomic_reps = None  # lazy — loaded when FCHL first used
        self.models_dir = models_dir
        self.dp5_mode = "fallback"
        self.fchl_kernel = ""  # set when the FCHL path runs: "qml" | "numpy"

    @property
    def atom_kde(self):
        """Lazy-build the unweighted atom-level KDE (DP5.py:98 fallback)."""
        from scipy.stats import gaussian_kde

        if self._atom_kde is None:
            self._atom_kde = gaussian_kde(self.folded_errors)
        return self._atom_kde

    # -- FCHL support (DevDoc appendix D / P4) ---------------------------

    @property
    def fchl_available(self) -> bool:
        """True when the FCHL-weighted atom path should run.

        Needs the FCHL assets **and** an active kernel backend: the compiled
        ``qml`` kernel (fast Fortran reference, used when importable), or the
        pure-numpy port (opt-in via ``ACP_FCHL_NUMPY=1`` — same math, but
        minutes/atom on the full 53 208-atom training set). When False the
        DP5 path degrades to the unweighted KDE fallback.
        """
        from acp.nmr.fchl import fchl_assets_available, fchl_kernel_active

        return fchl_kernel_active() and fchl_assets_available(self.models_dir)

    def _get_atomic_reps(self) -> np.ndarray:
        """Lazily load the training-set atom FCHL representations."""
        from acp.nmr.fchl import load_atomic_reps

        if self._atomic_reps is None:
            self._atomic_reps = load_atomic_reps(self.models_dir)
        return self._atomic_reps

    def atom_probability(self, scaled_error: float) -> float:
        """Per-atom DP5 probability (DP5.py:104-108).

        ``s_e_diff = |scaled_error - mean_abs_error|``
        ``p = kde.integrate_box_1d(mean - diff, mean + diff)``
        """
        diff = abs(float(scaled_error) - self.mean_abs_error)
        lo = self.mean_abs_error - diff
        hi = self.mean_abs_error + diff
        return float(self.atom_kde.integrate_box_1d(lo, hi))

    def atom_probability_fchl(
        self,
        representation: np.ndarray,
        scaled_error: float,
    ) -> float:
        """FCHL-weighted per-atom DP5 probability (DP5.py:85-108).

        Uses the atom's FCHL representation to weight the KDE against the
        training-set similarity. Falls back to the global KDE when ``qml``
        is unavailable (the caller should route via
        :meth:`probability_per_conformer_fchl`, which handles the switch).
        """
        from acp.nmr.fchl import atom_probability_fchl

        return atom_probability_fchl(
            representation,
            scaled_error,
            self.folded_errors,
            self.mean_abs_error,
            self._get_atomic_reps(),
        )

    def candidate_probability(self, atom_probs: list[float]) -> float:
        """Raw per-candidate DP5 before rescale (DP5.py:356-364).

        ``DP5scaled = 1 - gmean(1 - p_si)``
        """
        if not atom_probs:
            return 0.0
        from scipy.stats import gmean

        complements = [1.0 - p for p in atom_probs]
        # gmean of complements; guard against zeros
        if any(c <= 0 for c in complements):
            return 1.0
        return float(1.0 - gmean(complements))

    def rescale(self, raw_dp5: float) -> float:
        """Bayesian rescale (DP5.py:381).

        ``DP5 = 1 - incorrect.pdf(x) / (incorrect.pdf(x) + correct.pdf(x))``
        """
        x = np.array([[raw_dp5]])
        ip = float(self._incorrect_kde.pdf(x)[0])
        cp = float(self._correct_kde.pdf(x)[0])
        denom = ip + cp
        if denom <= 0:
            return 0.5
        return float(1.0 - ip / denom)

    def probability(self, scaled_errors: list[float]) -> float:
        """Full DP5 pipeline on a single (already-averaged) residual set.

        Simplified path: atom probs → gmean → rescale. Use
        :meth:`probability_per_conformer` for the Goodman-faithful path
        where KDE is evaluated per conformer before probability averaging.
        """
        self.dp5_mode = "fallback"
        atom_probs = [self.atom_probability(abs(se)) for se in scaled_errors]
        raw = self.candidate_probability(atom_probs)
        return self.rescale(raw)

    def probability_per_conformer(
        self,
        conformer_calc_shifts: list[list[float]],
        exp_shifts: list[float],
        boltzmann_weights: list[float],
    ) -> float:
        """Goodman-faithful DP5, unweighted-KDE atom path (DP5.py:73-141, 339-383).

        Per conformer: independently scale that conformer's calc shifts
        against ``exp_shifts`` (DP5.py:81-83), compute per-atom KDE
        probability (DP5.py:104-108), then **Boltzmann-average the
        probabilities across conformers** (DP5.py:339-353). The averaged
        per-atom probabilities are then geometric-mean combined
        (DP5.py:356-364) and Bayesian-rescaled (DP5.py:367-383).

        This differs from :meth:`probability` which evaluates the KDE
        once on Boltzmann-averaged shieldings. Since the KDE
        (``integrate_box_1d``) is nonlinear, ``avg(P(σ_i)) ≠ P(avg(σ_i))``.

        Args:
            conformer_calc_shifts: Per-conformer ¹³C calc shifts (TMS-
                converted), one list per conformer. Length = n_conformers.
            exp_shifts: Experimental ¹³C shifts (same length as each
                conformer's calc list).
            boltzmann_weights: Boltzmann weights per conformer
                (sum to 1; length = n_conformers).

        Returns:
            DP5 probability in ``[0, 1]``.
        """
        self.dp5_mode = "fallback"
        return self._probability_per_conformer(conformer_calc_shifts, exp_shifts, boltzmann_weights)

    def probability_per_conformer_fchl(
        self,
        conformer_calc_shifts: list[list[float]],
        exp_shifts: list[float],
        boltzmann_weights: list[float],
        conformer_reps: list[list[np.ndarray]],
    ) -> float:
        """Goodman-faithful DP5 with the FCHL-weighted atom path.

        Same per-conformer scaling + Boltzmann averaging as
        :meth:`probability_per_conformer`, but each atom's probability uses
        its FCHL19 representation to weight the KDE against the training set
        (``DP5.py:85-108``; see :meth:`atom_probability_fchl`). Requires the
        FCHL assets (``atomic_reps.gz``); the kernel runs on ``qml`` when
        importable, else the pure-numpy port.

        Args:
            conformer_calc_shifts: Per-conformer ¹³C calc shifts.
            exp_shifts: Experimental ¹³C shifts.
            boltzmann_weights: Boltzmann weights per conformer.
            conformer_reps: Per-conformer FCHL19 representations of the
                ¹³C atoms (parallel to ``conformer_calc_shifts``);
                ``conformer_reps[c][i]`` = descriptor of atom *i* in
                conformer *c*.

        Returns:
            DP5 probability in ``[0, 1]``.
        """
        if not self.fchl_available:
            raise RuntimeError(
                "FCHL-weighted DP5 requires the FCHL assets (atomic_reps.gz/"
                "frag_reps.gz). Use probability_per_conformer() for the "
                "fallback path."
            )
        if len(conformer_reps) != len(conformer_calc_shifts):
            raise ValueError("conformer_reps and conformer_calc_shifts lengths differ")
        from acp.nmr.fchl import kernel_backend

        self.dp5_mode = "fchl"
        self.fchl_kernel = kernel_backend()
        return self._probability_per_conformer(
            conformer_calc_shifts,
            exp_shifts,
            boltzmann_weights,
            conformer_reps=conformer_reps,
        )

    def _probability_per_conformer(
        self,
        conformer_calc_shifts: list[list[float]],
        exp_shifts: list[float],
        boltzmann_weights: list[float],
        conformer_reps: list[list[np.ndarray]] | None = None,
    ) -> float:
        """Shared per-conformer DP5 pipeline (DP5.py:73-141, 339-383).

        When *conformer_reps* is provided, per-atom probabilities use the
        FCHL-weighted KDE; otherwise the unweighted global KDE fallback.
        """
        import numpy as np
        from scipy.stats import linregress

        n_atoms = len(exp_shifts)
        if n_atoms == 0 or not conformer_calc_shifts:
            return 0.0

        use_fchl = conformer_reps is not None

        # Per-conformer: scale + per-atom KDE probability (DP5.py:75-110)
        # Boltzmann-average the probabilities (DP5.py:339-353)
        avg_atom_probs = [0.0] * n_atoms
        for conf_idx, (conf_shifts, weight) in enumerate(
            zip(conformer_calc_shifts, boltzmann_weights)
        ):
            if len(conf_shifts) != n_atoms:
                continue
            if n_atoms >= 2:
                slope, intercept, _, _, _ = linregress(exp_shifts, conf_shifts)
                if slope == 0 or not np.isfinite(slope):
                    slope, intercept = 1.0, 0.0
                scaled = [(c - intercept) / slope for c in conf_shifts]
            else:
                scaled = list(conf_shifts)
            for i in range(n_atoms):
                err = abs(scaled[i] - exp_shifts[i])
                if use_fchl:
                    p = self.atom_probability_fchl(conformer_reps[conf_idx][i], err)  # type: ignore[index]
                else:
                    p = self.atom_probability(err)
                avg_atom_probs[i] += weight * p

        # Candidate-level: gmean combine (DP5.py:356-364) + rescale (DP5.py:381)
        raw = self.candidate_probability(avg_atom_probs)
        return self.rescale(raw)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def load_error_model(
    model_id: str,
    model_path: Path | None = None,
) -> ErrorModel:
    """Resolve a DP4 :class:`ErrorModel` by id."""
    mid = (model_id or "").strip().lower()
    if mid.startswith("placeholder"):
        return PlaceholderStudentTErrorModel()
    if mid in ("", "goodman-legacy"):
        return GoodmanErrorModel()
    raise ValueError(f"Unknown DP4 error model: {model_id}")


def load_dp5_model(models_dir: Path | None = None) -> GoodmanDP5Model:
    """Load the Goodman DP5 KDE model (raises if model files missing)."""
    return GoodmanDP5Model(models_dir)


def dp5_model_available(models_dir: Path | None = None) -> bool:
    """Return True when the Goodman DP5 model files are present."""
    d = models_dir or _MODELS_DIR
    return all(
        (d / name).exists()
        for name in ("folded_scaled_errors.p", "c_w_kde_mean_s_0.025.p", "i_w_kde_mean_s_0.025.p")
    )


def dp5_fchl_available(models_dir: Path | None = None) -> bool:
    """Return True when the FCHL-weighted DP5 path will actually run.

    Needs the FCHL assets **and** an active kernel backend. The kernel runs
    on ``qml`` (compiled Fortran, fast) when importable; otherwise on the
    pure-numpy port when opted in via ``ACP_FCHL_NUMPY=1`` (same math, but
    minutes/atom on the full training set). When False the DP5 path degrades
    to the unweighted KDE fallback.
    """
    from acp.nmr.fchl import fchl_assets_available, fchl_kernel_active

    d = models_dir or _MODELS_DIR
    return fchl_kernel_active() and fchl_assets_available(d)


__all__ = [
    "ErrorModel",
    "PlaceholderStudentTErrorModel",
    "GoodmanErrorModel",
    "GoodmanDP5Model",
    "load_error_model",
    "load_dp5_model",
    "dp5_model_available",
    "dp5_fchl_available",
    "validate_error_model_binding",
]
