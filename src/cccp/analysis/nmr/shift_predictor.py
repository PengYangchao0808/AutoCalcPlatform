# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedCallResult=false
"""Chemical-shift predictor protocol + independent registry (todo 55 / gap G17).

``ShiftPredictor`` is the model-isolation seam for the DP5q-class research
route: per-atom predicted shifts keyed by an explicit atom mapping, an
optional (never fabricated) predictive distribution, declared input-geometry
requirements and the predictor's own model provenance.  Predictors live in an
INDEPENDENT registry with their own model ids and calibrators — separate from
the GIAO/Goodman statistical models in ``acp.nmr.error_model``.

Boundary (hard rules, gap §7 / G17):

* This layer does NOT replace the GIAO/DP4/DP5 chain.
* A predictor is never selected by the default NMR workflow.
* Speed alone is never a reason to substitute a predictor for the
  calculation chain.
* Predicted shifts — and any residuals derived from them — must never be fed
  into the Goodman Gaussian error models (``acp.nmr.error_model``); the
  isolation is guarded by ``tests/test_acp_nmr_shift_predictor.py``.
* Calibration claims are honest: a model with no calibration data reports
  ``calibration_status="uncalibrated"``, and ``"calibrated"`` requires a
  named calibrator and a calibration reference.
* The registry is consumed only by explicit callers (screening /
  prioritization experiments); importing this module registers nothing.

This module imports nothing from the ACP/CCCP calculation chain (stdlib +
typing only), so it stays exercisable in isolation.  Handoff: todo 56 (DP5q
stub adapter) and todo 57 (evaluation hooks) build on these types.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "CALIBRATION_STATUSES",
    "CalibrationStatus",
    "DuplicateShiftPredictorError",
    "GeometryRequirements",
    "InvalidShiftPredictorError",
    "PredictedShift",
    "PredictorGeometry",
    "ShiftDistribution",
    "ShiftModelProvenance",
    "ShiftPrediction",
    "ShiftPredictionRequest",
    "ShiftPredictor",
    "ShiftPredictorError",
    "UnknownShiftPredictorError",
    "get_shift_predictor",
    "list_shift_predictors",
    "register_shift_predictor",
    "unregister_shift_predictor",
]

#: Closed vocabulary of calibration statuses: ``"calibrated"`` means
#: calibration data + procedure exist and are referenced; ``"uncalibrated"``
#: means no calibration data exists for this model/domain (honest default).
CALIBRATION_STATUSES: Final[tuple[str, ...]] = ("calibrated", "uncalibrated")

CalibrationStatus = Literal["calibrated", "uncalibrated"]


# ---------------------------------------------------------------------------
# typed validation helpers
# ---------------------------------------------------------------------------


def _require_text(value: object, field_name: str) -> str:
    """Return *value* as a non-blank string; reject everything else."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string, got {value!r}")
    return value


def _require_finite(value: object, field_name: str) -> float:
    """Return *value* as a finite float; reject booleans and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return number


def _require_coordinate(value: object, field_name: str) -> tuple[float, float, float]:
    """Return *value* as a finite ``(x, y, z)`` triple."""
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{field_name} must be an (x, y, z) triple, got {value!r}")
    x, y, z = value
    return (
        _require_finite(x, f"{field_name}.x"),
        _require_finite(y, f"{field_name}.y"),
        _require_finite(z, f"{field_name}.z"),
    )


# ---------------------------------------------------------------------------
# typed records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShiftModelProvenance:
    """Immutable provenance of one predictor (honest calibration status).

    Attributes:
        model_id: Registry identity of the predictor (non-blank).
        version: Model version as reported by its authors (non-blank).
        calibration_status: Closed vocabulary — ``"calibrated"`` (calibration
            data + procedure exist and are referenced) or ``"uncalibrated"``
            (no calibration data for this model/domain: the honest default).
        calibrator: Who/what calibrated this deployment. Required for
            ``"calibrated"``; forbidden for ``"uncalibrated"`` (there is no
            calibrator to name).
        calibration_reference: Identifier of the calibration evidence
            (dataset id / asset digest / campaign id). Required for
            ``"calibrated"``; forbidden otherwise.
        notes: Optional free-text notes (documented limitations).
    """

    model_id: str
    version: str
    calibration_status: CalibrationStatus
    calibrator: str | None = None
    calibration_reference: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        _require_text(self.model_id, "ShiftModelProvenance.model_id")
        _require_text(self.version, "ShiftModelProvenance.version")
        if self.calibration_status not in CALIBRATION_STATUSES:
            raise ValueError(
                f"unknown calibration status {self.calibration_status!r}; "
                f"expected one of {CALIBRATION_STATUSES}"
            )
        if self.calibration_status == "calibrated":
            if self.calibrator is None or not self.calibrator.strip():
                raise ValueError(
                    "a calibrated model must name its calibrator — do not claim "
                    "calibration without calibration data"
                )
            if self.calibration_reference is None or not self.calibration_reference.strip():
                raise ValueError(
                    "a calibrated model must carry a calibration reference "
                    "(dataset id / asset digest / campaign id)"
                )
        else:
            if self.calibrator is not None:
                raise ValueError(
                    "an uncalibrated model must not carry a calibrator "
                    "(calibration was never established)"
                )
            if self.calibration_reference is not None:
                raise ValueError("an uncalibrated model must not carry a calibration reference")
        if self.notes:
            _require_text(self.notes, "ShiftModelProvenance.notes")

    def as_dict(self) -> dict[str, object]:
        """JSON-safe provenance view (every declared field, exact values)."""
        return {
            "model_id": self.model_id,
            "version": self.version,
            "calibration_status": self.calibration_status,
            "calibrator": self.calibrator,
            "calibration_reference": self.calibration_reference,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class GeometryRequirements:
    """Declared input-geometry requirements of one predictor.

    Attributes:
        min_conformers: Minimum number of input conformers the predictor
            consumes (``>= 1``).
        requires_optimized_geometry: True when a QC-optimized geometry is
            expected (a raw embedding is not sufficient).
        geometry_level: Human-readable level declaration (e.g. ``"any"``,
            ``"xtb-GFN2"``, ``"DFT-optimized"``) — recorded, never enforced
            by this module.
        requires_explicit_hydrogens: True when H atoms must be present.
    """

    min_conformers: int = 1
    requires_optimized_geometry: bool = False
    geometry_level: str = "any"
    requires_explicit_hydrogens: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.min_conformers, bool) or not isinstance(self.min_conformers, int):
            raise ValueError("GeometryRequirements.min_conformers must be an int")
        if self.min_conformers < 1:
            raise ValueError("GeometryRequirements.min_conformers must be >= 1")
        if not isinstance(self.requires_optimized_geometry, bool):
            raise ValueError("GeometryRequirements.requires_optimized_geometry must be a bool")
        if not isinstance(self.requires_explicit_hydrogens, bool):
            raise ValueError("GeometryRequirements.requires_explicit_hydrogens must be a bool")
        _require_text(self.geometry_level, "GeometryRequirements.geometry_level")

    def as_dict(self) -> dict[str, object]:
        """JSON-safe requirements view (every declared field, exact values)."""
        return {
            "min_conformers": self.min_conformers,
            "requires_optimized_geometry": self.requires_optimized_geometry,
            "geometry_level": self.geometry_level,
            "requires_explicit_hydrogens": self.requires_explicit_hydrogens,
        }


@dataclass(frozen=True)
class ShiftDistribution:
    """Typed predictive distribution for one predicted shift (never fabricated).

    At least one uncertainty term (``std_ppm`` or ``quantiles``) must be
    present; a spread-less record is not a distribution — models that cannot
    supply uncertainty leave :attr:`PredictedShift.distribution` as ``None``
    instead of fabricating a zero spread.

    Attributes:
        mean_ppm: Point estimate (mean/median) in ppm.
        std_ppm: Standard deviation in ppm (``None`` when not supplied).
        quantiles: ``(probability_level, ppm)`` pairs with strictly
            increasing levels in ``(0, 1)``.
    """

    mean_ppm: float
    std_ppm: float | None = None
    quantiles: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        mean = _require_finite(self.mean_ppm, "ShiftDistribution.mean_ppm")
        std: float | None = None
        if self.std_ppm is not None:
            std = _require_finite(self.std_ppm, "ShiftDistribution.std_ppm")
            if std < 0:
                raise ValueError("ShiftDistribution.std_ppm must be >= 0")
        quantiles: list[tuple[float, float]] = []
        for item in self.quantiles:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("ShiftDistribution.quantiles entries must be (level, ppm) pairs")
            level = _require_finite(item[0], "ShiftDistribution.quantiles.level")
            if not 0.0 < level < 1.0:
                raise ValueError("ShiftDistribution quantile levels must lie in (0, 1)")
            value = _require_finite(item[1], "ShiftDistribution.quantiles.value")
            quantiles.append((level, value))
        levels = [level for level, _ in quantiles]
        if levels != sorted(levels) or len(set(levels)) != len(levels):
            raise ValueError("ShiftDistribution quantile levels must be strictly increasing")
        if std is None and not quantiles:
            raise ValueError(
                "ShiftDistribution requires at least one uncertainty term "
                "(std_ppm or quantiles); a spread-less record is not a distribution"
            )
        object.__setattr__(self, "mean_ppm", mean)
        object.__setattr__(self, "std_ppm", std)
        object.__setattr__(self, "quantiles", tuple(quantiles))


@dataclass(frozen=True)
class PredictorGeometry:
    """One input geometry with an EXPLICIT atom mapping.

    ``atom_uids[i]`` identifies ``symbols[i]`` and ``coordinates[i]``; the
    prediction keys come from this mapping, never from list position alone.

    Attributes:
        symbols: Element symbols, one per atom.
        coordinates: ``(x, y, z)`` triples in Angstrom, one per atom.
        atom_uids: Stable atom identities (the explicit mapping), unique and
            parallel to ``symbols``/``coordinates``.
        level: Declared geometry provenance level (e.g. ``"xtb-GFN2"``).
    """

    symbols: tuple[str, ...]
    coordinates: tuple[tuple[float, float, float], ...]
    atom_uids: tuple[str, ...]
    level: str = "unknown"

    def __post_init__(self) -> None:
        symbols = tuple(self.symbols)
        coordinates = tuple(
            _require_coordinate(coordinate, "PredictorGeometry.coordinates[]")
            for coordinate in self.coordinates
        )
        atom_uids = tuple(self.atom_uids)
        if not symbols:
            raise ValueError("PredictorGeometry must contain at least one atom")
        if not (len(symbols) == len(coordinates) == len(atom_uids)):
            raise ValueError(
                "PredictorGeometry symbols/coordinates/atom_uids length mismatch: "
                f"{len(symbols)}/{len(coordinates)}/{len(atom_uids)}"
            )
        for symbol in symbols:
            _require_text(symbol, "PredictorGeometry.symbols[]")
        for atom_uid in atom_uids:
            _require_text(atom_uid, "PredictorGeometry.atom_uids[]")
        if len(set(atom_uids)) != len(atom_uids):
            raise ValueError("PredictorGeometry.atom_uids contains duplicate entries")
        _require_text(self.level, "PredictorGeometry.level")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "coordinates", coordinates)
        object.__setattr__(self, "atom_uids", atom_uids)


@dataclass(frozen=True)
class ShiftPredictionRequest:
    """One prediction request: target nucleus + per-conformer geometries.

    Every conformer must share the identical ``atom_uids`` mapping (same
    identities, same order) — a predictor never receives a scrambled mapping.

    Attributes:
        nucleus: Target nucleus label (e.g. ``"13C"``).
        geometries: One :class:`PredictorGeometry` per input conformer
            (at least one).
        charge: Total charge context.
        multiplicity: Spin multiplicity (``>= 1``).
    """

    nucleus: str
    geometries: tuple[PredictorGeometry, ...]
    charge: int = 0
    multiplicity: int = 1

    def __post_init__(self) -> None:
        _require_text(self.nucleus, "ShiftPredictionRequest.nucleus")
        geometries = tuple(self.geometries)
        if not geometries:
            raise ValueError("ShiftPredictionRequest requires at least one geometry")
        for geometry in geometries:
            if not isinstance(geometry, PredictorGeometry):
                raise ValueError(
                    "ShiftPredictionRequest.geometries entries must be PredictorGeometry records"
                )
        reference = geometries[0].atom_uids
        for geometry in geometries[1:]:
            if geometry.atom_uids != reference:
                raise ValueError(
                    "all conformers must share the same atom mapping (atom_uids identity and order)"
                )
        if isinstance(self.charge, bool) or not isinstance(self.charge, int):
            raise ValueError("ShiftPredictionRequest.charge must be an int")
        if (
            isinstance(self.multiplicity, bool)
            or not isinstance(self.multiplicity, int)
            or self.multiplicity < 1
        ):
            raise ValueError("ShiftPredictionRequest.multiplicity must be an int >= 1")
        object.__setattr__(self, "geometries", geometries)


@dataclass(frozen=True)
class PredictedShift:
    """One atom's predicted shift + optional (never fabricated) distribution."""

    shift_ppm: float
    distribution: ShiftDistribution | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "shift_ppm", _require_finite(self.shift_ppm, "PredictedShift.shift_ppm")
        )
        if self.distribution is not None and not isinstance(self.distribution, ShiftDistribution):
            raise ValueError(
                "PredictedShift.distribution must be a ShiftDistribution record or None"
            )


@dataclass(frozen=True)
class ShiftPrediction:
    """One predictor's output: per-atom records keyed by explicit atom mapping.

    ``shifts`` maps ``atom_uid`` → :class:`PredictedShift` — bare floats are
    rejected (typed records only). The mapping is copied at construction so
    later mutations of the caller's dict do not leak into the frozen record.

    Attributes:
        nucleus: Requested nucleus label (echoed for provenance).
        model: Provenance of the predictor that produced this output.
        geometry_requirements: The requirements the predictor declares.
        shifts: ``atom_uid`` → :class:`PredictedShift`.
    """

    nucleus: str
    model: ShiftModelProvenance
    geometry_requirements: GeometryRequirements
    shifts: Mapping[str, PredictedShift]

    def __post_init__(self) -> None:
        _require_text(self.nucleus, "ShiftPrediction.nucleus")
        if not isinstance(self.model, ShiftModelProvenance):
            raise ValueError("ShiftPrediction.model must be a ShiftModelProvenance record")
        if not isinstance(self.geometry_requirements, GeometryRequirements):
            raise ValueError(
                "ShiftPrediction.geometry_requirements must be a GeometryRequirements record"
            )
        shifts: dict[str, PredictedShift] = {}
        for atom_uid, record in self.shifts.items():
            _require_text(atom_uid, "ShiftPrediction.shifts key")
            if not isinstance(record, PredictedShift):
                raise ValueError(
                    "ShiftPrediction.shifts values must be PredictedShift records "
                    f"(bare values are forbidden), got {type(record).__name__} "
                    f"for {atom_uid!r}"
                )
            shifts[atom_uid] = record
        object.__setattr__(self, "shifts", shifts)


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ShiftPredictor(Protocol):
    """Independent chemical-shift predictor (DP5q-class research route).

    Implementations declare their own :class:`ShiftModelProvenance` and
    :class:`GeometryRequirements`, and map a :class:`ShiftPredictionRequest`
    (explicit atom mapping + conformer geometries) to a
    :class:`ShiftPrediction`.  This protocol NEVER replaces the GIAO/DP4/DP5
    chain: it exists for screening / prioritization experiments, and its
    residuals must not enter the Goodman statistical error models (see the
    module docstring for the full boundary).
    """

    @property
    def model_provenance(self) -> ShiftModelProvenance:
        """Model identity, version and honest calibration status."""
        ...

    @property
    def geometry_requirements(self) -> GeometryRequirements:
        """Declared input-geometry requirements (conformer count/level)."""
        ...

    def predict(self, request: ShiftPredictionRequest) -> ShiftPrediction:
        """Predict per-atom shifts keyed by the request's explicit atom mapping."""
        ...


# ---------------------------------------------------------------------------
# independent registry (own model ids + calibrators; no error-model coupling)
# ---------------------------------------------------------------------------

#: Protocol members every registration must expose.
_PREDICTOR_PROTOCOL_MEMBERS: Final[tuple[str, ...]] = (
    "model_provenance",
    "geometry_requirements",
    "predict",
)

#: The predictor registry. Starts EMPTY by design: nothing is auto-wired into
#: the default NMR workflow, and only explicit callers register/consume.
_SHIFT_PREDICTORS: dict[str, ShiftPredictor] = {}


class ShiftPredictorError(ValueError):
    """Base class for typed shift-predictor failures."""


class InvalidShiftPredictorError(ShiftPredictorError):
    """The object does not satisfy the ShiftPredictor protocol/record contract."""


class DuplicateShiftPredictorError(ShiftPredictorError):
    """A predictor is already registered under the same model id."""


class UnknownShiftPredictorError(ShiftPredictorError):
    """No predictor is registered under the requested model id."""


def _validated_model_id(predictor: object) -> str:
    """Validate the protocol contract and return the registration model id."""
    if not isinstance(predictor, ShiftPredictor):
        missing = [name for name in _PREDICTOR_PROTOCOL_MEMBERS if not hasattr(predictor, name)]
        detail = f"missing member(s): {missing}" if missing else "member check failed"
        raise InvalidShiftPredictorError(
            f"object does not satisfy the ShiftPredictor protocol ({detail})"
        )
    provenance = predictor.model_provenance
    if not isinstance(provenance, ShiftModelProvenance):
        raise InvalidShiftPredictorError(
            "ShiftPredictor.model_provenance must be a ShiftModelProvenance record, "
            f"got {type(provenance).__name__}"
        )
    geometry = predictor.geometry_requirements
    if not isinstance(geometry, GeometryRequirements):
        raise InvalidShiftPredictorError(
            "ShiftPredictor.geometry_requirements must be a GeometryRequirements record, "
            f"got {type(geometry).__name__}"
        )
    return provenance.model_id.strip()


def register_shift_predictor(predictor: ShiftPredictor, *, replace: bool = False) -> str:
    """Register *predictor* under its provenance ``model_id``; returns the id.

    Registration never silently overwrites: an already-registered id raises
    unless *replace* is True.

    Raises:
        InvalidShiftPredictorError: The object does not satisfy the protocol
            or does not carry the typed provenance/geometry records.
        DuplicateShiftPredictorError: The model id is already registered and
            *replace* is False.
    """
    model_id = _validated_model_id(predictor)
    if model_id in _SHIFT_PREDICTORS and not replace:
        raise DuplicateShiftPredictorError(
            f"shift predictor {model_id!r} is already registered; pass "
            "replace=True (or unregister first) to overwrite"
        )
    _SHIFT_PREDICTORS[model_id] = predictor
    logger.debug("registered shift predictor %r (replace=%s)", model_id, replace)
    return model_id


def get_shift_predictor(model_id: str) -> ShiftPredictor:
    """Return the predictor registered under *model_id*.

    Raises:
        UnknownShiftPredictorError: No predictor is registered under this id
            (the message lists the currently registered ids).
    """
    key = (model_id or "").strip()
    predictor = _SHIFT_PREDICTORS.get(key)
    if predictor is None:
        available = ", ".join(list_shift_predictors()) or "none"
        raise UnknownShiftPredictorError(
            f"unknown shift predictor {model_id!r}; registered predictors: {available}"
        )
    return predictor


def list_shift_predictors() -> tuple[str, ...]:
    """Return the sorted model ids currently registered (empty by default)."""
    return tuple(sorted(_SHIFT_PREDICTORS))


def unregister_shift_predictor(model_id: str) -> bool:
    """Remove one registration; returns whether it existed (no-op otherwise)."""
    key = (model_id or "").strip()
    removed = _SHIFT_PREDICTORS.pop(key, None)
    if removed is not None:
        logger.debug("unregistered shift predictor %r", key)
    return removed is not None
