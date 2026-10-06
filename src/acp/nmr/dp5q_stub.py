# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Isolated DP5q stub adapter — optional, explicitly-selected screening aid.

Todo 56 / gap §7 (G17).  The DP5q research route (uncertainty-calibrated graph
nets as a DFT cost shortcut) is represented here by a deliberately minimal,
inference-only stub adapter on top of the todo-55 ``ShiftPredictor`` protocol.
It exists so screening / prioritization experiments (todo 57) can develop
against the real seams without ever touching the default NMR chain.

Hard boundaries:

* **Optional dependency, lazily imported** — importing this module never
  imports the DP5q runtime and never raises; :func:`dp5q_stub_status` reports
  a typed ``available`` / ``unavailable`` state with an explicit reason, and
  :func:`load_dp5q_stub_predictor` / :func:`register_dp5q_stub` refuse with
  :class:`Dp5qStubUnavailableError` when the runtime is missing.  There is no
  silent fallback to any other model.
* **Loadable, never trained** — this module contains no training code: no ML
  framework is imported, nothing is ingested, and the only file ever read is
  the packaged parameter JSON (:data:`DP5Q_STUB_PARAMETERS_PATH`).  Without
  packaged parameters the stub still loads, but refuses to predict with
  :class:`Dp5qStubNotParameterizedError` instead of fabricating values.
* **Never a default** — this module imports nothing from the workflow,
  error-model or calculation chain, and importing it registers nothing in the
  todo-55 registry; only an explicit :func:`register_dp5q_stub` call does.
  The default DP4/DP5 path is guarded (by tests) to ignore the registry, so
  the stub can never take over the default shielding task or the Goodman chain.
* **Independent provenance** — the stub carries its own pinned ``model_id`` /
  ``version`` and is honestly ``"uncalibrated"`` (no calibration data exists
  for this deployment).  It is never presented as calibrated and never a
  substitute for the GIAO/DP4/DP5 calculation chain.

Handoff: todo 57 (evaluation hooks) consumes :func:`dp5q_stub_status` for
availability receipts and :func:`load_dp5q_stub_predictor` for explicit,
opt-in screening experiments.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final

from acp.nmr.shift_predictor import (
    GeometryRequirements,
    PredictedShift,
    ShiftDistribution,
    ShiftModelProvenance,
    ShiftPrediction,
    ShiftPredictionRequest,
    register_shift_predictor,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DP5Q_STUB_GEOMETRY_REQUIREMENTS",
    "DP5Q_STUB_MODEL_ID",
    "DP5Q_STUB_OPTIONAL_DEPENDENCY",
    "DP5Q_STUB_PARAMETERS_PATH",
    "DP5Q_STUB_PROVENANCE",
    "DP5Q_STUB_STATUSES",
    "DP5Q_STUB_UNAVAILABLE_REASONS",
    "DP5Q_STUB_VERSION",
    "Dp5qShiftEntry",
    "Dp5qStubError",
    "Dp5qStubNotParameterizedError",
    "Dp5qStubParameterError",
    "Dp5qStubParameters",
    "Dp5qStubPredictor",
    "Dp5qStubStatus",
    "Dp5qStubUnavailableError",
    "dp5q_stub_available",
    "dp5q_stub_status",
    "load_dp5q_stub_predictor",
    "register_dp5q_stub",
]

#: Registry identity of the stub adapter — independent from every existing
#: NMR model id (the Goodman chain keeps its own ids and calibrators).
DP5Q_STUB_MODEL_ID: Final[str] = "dp5q-stub"

#: Pinned stub version.  It is deliberately not a claim about any upstream
#: DP5q release: the stub is loadability/evaluation scaffolding, not a model.
DP5Q_STUB_VERSION: Final[str] = "0.0.0-stub"

#: Import name of the OPTIONAL DP5q runtime.  It is imported lazily and only
#: by explicit calls; absence is reported as a typed unavailable state.
DP5Q_STUB_OPTIONAL_DEPENDENCY: Final[str] = "dp5q"

#: Closed vocabulary of :class:`Dp5qStubStatus` states.
DP5Q_STUB_STATUSES: Final[tuple[str, ...]] = ("available", "unavailable")

#: Closed vocabulary of unavailability reasons (an unavailable state always
#: carries one of these plus an explicit human-readable detail).
DP5Q_STUB_UNAVAILABLE_REASONS: Final[tuple[str, ...]] = (
    "optional_dependency_missing",
    "optional_dependency_broken",
)

#: Packaged (optional) lookup parameters for the stub — read-only, the only
#: file this module ever reads.  Its absence keeps the stub loadable but
#: explicitly not parameterized.
_MODELS_DIR: Final[Path] = Path(__file__).resolve().parent / "models"
DP5Q_STUB_PARAMETERS_PATH: Final[Path] = _MODELS_DIR / "dp5q_stub_params.json"

#: Declared geometry requirements (carried through the todo-55 typed
#: descriptor).  DP5q-class prediction is sold as a cheap-geometry route, so
#: no optimized geometry is required; requirements are declared, never
#: enforced by this module.
DP5Q_STUB_GEOMETRY_REQUIREMENTS: Final[GeometryRequirements] = GeometryRequirements(
    min_conformers=1,
    requires_optimized_geometry=False,
    geometry_level="any",
    requires_explicit_hydrogens=True,
)

#: Honest provenance: no calibration data exists for this deployment, so the
#: stub is ``uncalibrated`` and must never claim a calibrator or reference
#: (the todo-55 record enforces both directions).
DP5Q_STUB_PROVENANCE: Final[ShiftModelProvenance] = ShiftModelProvenance(
    model_id=DP5Q_STUB_MODEL_ID,
    version=DP5Q_STUB_VERSION,
    calibration_status="uncalibrated",
    notes=(
        "inference-only stub adapter for screening/prioritization experiments; "
        "loadable but never trained in ACP; predictions never feed the "
        "statistical error models and never substitute for the calculation chain"
    ),
)


# ---------------------------------------------------------------------------
# typed errors
# ---------------------------------------------------------------------------


class Dp5qStubError(RuntimeError):
    """Base class for typed DP5q stub adapter failures."""


class Dp5qStubUnavailableError(Dp5qStubError):
    """The optional DP5q runtime is absent/broken (never a silent fallback).

    Carries the typed :class:`Dp5qStubStatus` so callers can record the exact
    reason in receipts.
    """

    def __init__(self, status: Dp5qStubStatus) -> None:
        self.status = status
        super().__init__(status.detail or status.reason or "the DP5q stub is unavailable")


class Dp5qStubNotParameterizedError(Dp5qStubError):
    """The stub loaded, but no packaged parameters exist (it never trains them)."""


class Dp5qStubParameterError(Dp5qStubError, ValueError):
    """Malformed packaged parameters, or a lookup outside the parameter table."""


# ---------------------------------------------------------------------------
# typed records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dp5qStubStatus:
    """Typed availability of the optional DP5q stub adapter.

    ``status`` is one of :data:`DP5Q_STUB_STATUSES`.  An ``unavailable``
    state carries a closed-vocabulary ``reason`` plus an explicit ``detail``
    and never a dependency version; an ``available`` state reports the
    detected runtime version (when it declares one) and never a reason.
    """

    status: str
    reason: str | None = None
    detail: str | None = None
    dependency: str = DP5Q_STUB_OPTIONAL_DEPENDENCY
    dependency_version: str | None = None
    parameters_path: str | None = None
    parameters_present: bool = False

    def __post_init__(self) -> None:
        if self.status not in DP5Q_STUB_STATUSES:
            raise ValueError(
                f"unknown DP5q stub status {self.status!r}; expected one of {DP5Q_STUB_STATUSES}"
            )
        if self.status == "available":
            if self.reason is not None:
                raise ValueError("an available DP5q stub must not carry an unavailability reason")
            if self.detail is not None:
                raise ValueError("an available DP5q stub must not carry an unavailability detail")
        else:
            if self.reason not in DP5Q_STUB_UNAVAILABLE_REASONS:
                raise ValueError(
                    f"unknown DP5q stub unavailability reason {self.reason!r}; expected one "
                    f"of {DP5Q_STUB_UNAVAILABLE_REASONS}"
                )
            if not self.detail or not self.detail.strip():
                raise ValueError("an unavailable DP5q stub must carry an explicit detail")
            if self.dependency_version is not None:
                raise ValueError("an unavailable DP5q stub cannot report a dependency version")
        if not isinstance(self.parameters_present, bool):
            raise ValueError("Dp5qStubStatus.parameters_present must be a bool")

    @property
    def available(self) -> bool:
        """True only when the optional runtime imported successfully."""
        return self.status == "available"

    def as_dict(self) -> dict[str, object]:
        """JSON-safe status view (every declared field, exact values)."""
        return {
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "dependency": self.dependency,
            "dependency_version": self.dependency_version,
            "parameters_path": self.parameters_path,
            "parameters_present": self.parameters_present,
        }


def _require_finite(value: object, field_name: str) -> float:
    """Return *value* as a finite float; reject booleans and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Dp5qStubParameterError(f"{field_name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise Dp5qStubParameterError(f"{field_name} must be finite, got {value!r}")
    return number


@dataclass(frozen=True)
class Dp5qShiftEntry:
    """One packaged per-element shift entry (mean + optional spread).

    ``std_ppm`` is ``None`` when the packaged parameters declare no spread;
    consumers must then treat the prediction as spread-less rather than
    fabricating a zero-width distribution.
    """

    mean_ppm: float
    std_ppm: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "mean_ppm", _require_finite(self.mean_ppm, "Dp5qShiftEntry.mean_ppm")
        )
        if self.std_ppm is not None:
            std = _require_finite(self.std_ppm, "Dp5qShiftEntry.std_ppm")
            if std < 0:
                raise Dp5qStubParameterError("Dp5qShiftEntry.std_ppm must be >= 0")
            object.__setattr__(self, "std_ppm", std)

    def as_dict(self) -> dict[str, object]:
        """JSON-safe entry view."""
        return {"mean_ppm": self.mean_ppm, "std_ppm": self.std_ppm}


def _parse_entry(entry_payload: object, nucleus: str, element: str) -> Dp5qShiftEntry:
    """Parse one packaged parameter entry with typed, field-addressed errors."""
    if not isinstance(entry_payload, Mapping):
        raise Dp5qStubParameterError(
            f"packaged DP5q parameter for {nucleus}/{element} must be an object, "
            f"got {type(entry_payload).__name__}"
        )
    unknown = [key for key in entry_payload if key not in {"mean_ppm", "std_ppm"}]
    if unknown:
        raise Dp5qStubParameterError(
            f"packaged DP5q parameter for {nucleus}/{element} has unknown fields: "
            f"{sorted(map(str, unknown))}"
        )
    if "mean_ppm" not in entry_payload:
        raise Dp5qStubParameterError(
            f"packaged DP5q parameter for {nucleus}/{element} is missing 'mean_ppm'"
        )
    mean = _require_finite(entry_payload["mean_ppm"], f"parameters[{nucleus}][{element}].mean_ppm")
    std: float | None = None
    raw_std = entry_payload.get("std_ppm")
    if raw_std is not None:
        std = _require_finite(raw_std, f"parameters[{nucleus}][{element}].std_ppm")
        if std < 0:
            raise Dp5qStubParameterError(
                f"packaged DP5q parameter for {nucleus}/{element} has a negative std_ppm"
            )
    return Dp5qShiftEntry(mean_ppm=mean, std_ppm=std)


@dataclass(frozen=True)
class Dp5qStubParameters:
    """Packaged inference-only lookup parameters for the stub.

    The payload is a plain JSON object keyed per nucleus and element; there is
    no training data here and nothing in this module trains anything.  The
    payload may pin the stub identity (``model_id`` / ``version``) — a
    mismatch is rejected instead of being silently accepted.
    """

    nuclei: Mapping[str, Mapping[str, Dp5qShiftEntry]]

    def __post_init__(self) -> None:
        if not isinstance(self.nuclei, Mapping) or not self.nuclei:
            raise Dp5qStubParameterError("Dp5qStubParameters.nuclei must be a non-empty mapping")
        copied: dict[str, dict[str, Dp5qShiftEntry]] = {}
        for nucleus, table in self.nuclei.items():
            if not isinstance(nucleus, str) or not nucleus.strip():
                raise Dp5qStubParameterError(
                    f"Dp5qStubParameters nucleus keys must be non-blank strings, got {nucleus!r}"
                )
            if not isinstance(table, Mapping) or not table:
                raise Dp5qStubParameterError(
                    f"Dp5qStubParameters[{nucleus!r}] must be a non-empty element mapping"
                )
            entries: dict[str, Dp5qShiftEntry] = {}
            for element, entry in table.items():
                if not isinstance(element, str) or not element.strip():
                    raise Dp5qStubParameterError(
                        f"Dp5qStubParameters[{nucleus!r}] element keys must be non-blank "
                        f"strings, got {element!r}"
                    )
                if not isinstance(entry, Dp5qShiftEntry):
                    raise Dp5qStubParameterError(
                        f"Dp5qStubParameters[{nucleus!r}][{element!r}] must be a "
                        f"Dp5qShiftEntry record, got {type(entry).__name__}"
                    )
                entries[element] = entry
            copied[nucleus] = entries
        object.__setattr__(self, "nuclei", copied)

    @classmethod
    def from_payload(cls, payload: object) -> Dp5qStubParameters:
        """Parse a packaged parameter payload (typed errors, no silent coercion)."""
        if not isinstance(payload, Mapping):
            raise Dp5qStubParameterError(
                f"packaged DP5q parameters must be a JSON object, got {type(payload).__name__}"
            )
        unknown_top = [key for key in payload if key not in {"model_id", "version", "nuclei"}]
        if unknown_top:
            raise Dp5qStubParameterError(
                f"packaged DP5q parameters have unknown top-level fields: "
                f"{sorted(map(str, unknown_top))}"
            )
        model_id = payload.get("model_id", DP5Q_STUB_MODEL_ID)
        if model_id != DP5Q_STUB_MODEL_ID:
            raise Dp5qStubParameterError(
                f"packaged DP5q parameters declare model_id {model_id!r}; the stub is "
                f"{DP5Q_STUB_MODEL_ID!r} — refusing to load foreign model parameters"
            )
        version = payload.get("version", DP5Q_STUB_VERSION)
        if version != DP5Q_STUB_VERSION:
            raise Dp5qStubParameterError(
                f"packaged DP5q parameters declare version {version!r}; the stub is pinned "
                f"at {DP5Q_STUB_VERSION!r}"
            )
        nuclei_payload = payload.get("nuclei")
        if not isinstance(nuclei_payload, Mapping) or not nuclei_payload:
            raise Dp5qStubParameterError(
                "packaged DP5q parameters need a non-empty 'nuclei' object"
            )
        nuclei: dict[str, dict[str, Dp5qShiftEntry]] = {}
        for nucleus, table in nuclei_payload.items():
            if not isinstance(nucleus, str) or not nucleus.strip():
                raise Dp5qStubParameterError(
                    f"invalid nucleus key {nucleus!r} in packaged DP5q parameters"
                )
            if not isinstance(table, Mapping) or not table:
                raise Dp5qStubParameterError(f"nucleus {nucleus!r} needs a non-empty element table")
            entries: dict[str, Dp5qShiftEntry] = {}
            for element, entry_payload in table.items():
                if not isinstance(element, str) or not element.strip():
                    raise Dp5qStubParameterError(
                        f"invalid element key {element!r} under nucleus {nucleus!r}"
                    )
                entries[element] = _parse_entry(entry_payload, nucleus, element)
            nuclei[nucleus] = entries
        return cls(nuclei=nuclei)

    @classmethod
    def load(cls, parameters_path: Path) -> Dp5qStubParameters:
        """Read packaged parameters from *parameters_path* (the only file read)."""
        try:
            raw = parameters_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise Dp5qStubParameterError(
                f"cannot read packaged DP5q parameters at {parameters_path}: {exc}"
            ) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise Dp5qStubParameterError(
                f"packaged DP5q parameters at {parameters_path} are not valid JSON: {exc}"
            ) from exc
        return cls.from_payload(payload)

    def as_dict(self) -> dict[str, object]:
        """JSON-safe parameter view (nucleus -> element -> entry)."""
        return {
            "nuclei": {
                nucleus: {element: entry.as_dict() for element, entry in table.items()}
                for nucleus, table in self.nuclei.items()
            }
        }


# ---------------------------------------------------------------------------
# optional-runtime inspection (lazy; never at module import time)
# ---------------------------------------------------------------------------


def _import_optional_runtime() -> ModuleType:
    """Lazy import of the optional DP5q runtime (monkeypatch seam for tests)."""
    return importlib.import_module(DP5Q_STUB_OPTIONAL_DEPENDENCY)


def _resolved_parameters_path(parameters_path: Path | None) -> Path:
    return Path(parameters_path) if parameters_path is not None else DP5Q_STUB_PARAMETERS_PATH


def _inspect_optional_runtime(parameters_path: Path) -> tuple[ModuleType | None, Dp5qStubStatus]:
    """Import the optional runtime once and classify the outcome as a typed state."""
    parameters_present = parameters_path.is_file()
    try:
        runtime = _import_optional_runtime()
    except ModuleNotFoundError as exc:
        return None, Dp5qStubStatus(
            status="unavailable",
            reason="optional_dependency_missing",
            detail=(
                f"optional DP5q runtime {DP5Q_STUB_OPTIONAL_DEPENDENCY!r} is not installed "
                f"({exc}); the isolated stub adapter stays disabled"
            ),
            parameters_path=str(parameters_path),
            parameters_present=parameters_present,
        )
    except ImportError as exc:
        return None, Dp5qStubStatus(
            status="unavailable",
            reason="optional_dependency_broken",
            detail=(
                f"optional DP5q runtime {DP5Q_STUB_OPTIONAL_DEPENDENCY!r} is present but "
                f"not importable: {exc}"
            ),
            parameters_path=str(parameters_path),
            parameters_present=parameters_present,
        )
    version = getattr(runtime, "__version__", None)
    if not isinstance(version, str) or not version.strip():
        version = None
    return runtime, Dp5qStubStatus(
        status="available",
        dependency_version=version,
        parameters_path=str(parameters_path),
        parameters_present=parameters_present,
    )


def dp5q_stub_status(parameters_path: Path | None = None) -> Dp5qStubStatus:
    """Typed availability of the stub adapter — never raises, never imports eagerly.

    Absent/broken optional runtime and missing packaged parameters are
    explicit states; callers (todo 57 receipts) must never substitute
    placeholder values for an unavailable state.
    """
    return _inspect_optional_runtime(_resolved_parameters_path(parameters_path))[1]


def dp5q_stub_available(parameters_path: Path | None = None) -> bool:
    """True only when the optional DP5q runtime imported successfully."""
    return dp5q_stub_status(parameters_path=parameters_path).available


# ---------------------------------------------------------------------------
# inference-only stub adapter
# ---------------------------------------------------------------------------


class Dp5qStubPredictor:
    """Inference-only DP5q stub adapter (loadable; it never trains).

    Predictions come from the packaged parameter lookup only: each requested
    atom is mapped through the request's explicit ``atom_uids`` and its
    element symbol to one :class:`Dp5qShiftEntry`.  Only atoms whose element
    has a packaged entry for the requested nucleus receive predictions (a
    ``13C`` table covers carbons, not hydrogens); when no requested atom is
    covered — or the nucleus is absent from the table — the stub refuses with
    a typed error instead of returning an empty mapping or an invented shift.
    Conformer geometries do not modulate the lookup (a documented stub
    limitation) — the adapter is the evaluation seam, not a scientific
    surrogate.

    Construct through :func:`load_dp5q_stub_predictor`; direct construction
    exists for callers that already hold the imported runtime handle.
    """

    def __init__(
        self,
        *,
        runtime: object,
        parameters: Dp5qStubParameters | None = None,
        geometry_requirements: GeometryRequirements | None = None,
    ) -> None:
        if runtime is None:
            raise Dp5qStubParameterError(
                "Dp5qStubPredictor requires the imported optional runtime handle"
            )
        if parameters is not None and not isinstance(parameters, Dp5qStubParameters):
            raise Dp5qStubParameterError(
                "parameters must be a Dp5qStubParameters record or None, "
                f"got {type(parameters).__name__}"
            )
        if geometry_requirements is not None and not isinstance(
            geometry_requirements, GeometryRequirements
        ):
            raise Dp5qStubParameterError(
                "geometry_requirements must be a GeometryRequirements record or None, "
                f"got {type(geometry_requirements).__name__}"
            )
        self._runtime = runtime
        self._parameters = parameters
        self._geometry_requirements = geometry_requirements or DP5Q_STUB_GEOMETRY_REQUIREMENTS

    @property
    def model_provenance(self) -> ShiftModelProvenance:
        """Stub identity/version with its honest ``uncalibrated`` status."""
        return DP5Q_STUB_PROVENANCE

    @property
    def geometry_requirements(self) -> GeometryRequirements:
        """Declared input-geometry requirements (typed todo-55 descriptor)."""
        return self._geometry_requirements

    @property
    def parameters(self) -> Dp5qStubParameters | None:
        """Loaded packaged parameters (``None`` = loadable but not parameterized)."""
        return self._parameters

    def predict(self, request: ShiftPredictionRequest) -> ShiftPrediction:
        """Map the request's explicit atom mapping onto packaged shifts.

        Raises:
            Dp5qStubNotParameterizedError: No packaged parameters are loaded —
                this adapter never trains them, so it refuses instead of
                fabricating predictions.
            Dp5qStubParameterError: The request is not a todo-55 request
                record, or the parameter table has no entry for the requested
                nucleus/element (typed, field-addressed errors).
        """
        if not isinstance(request, ShiftPredictionRequest):
            raise Dp5qStubParameterError(
                f"predict expects a ShiftPredictionRequest, got {type(request).__name__}"
            )
        if self._parameters is None:
            raise Dp5qStubNotParameterizedError(
                "the DP5q stub is loadable but has no packaged parameters (it never "
                f"trains); supply them via {DP5Q_STUB_PARAMETERS_PATH}"
            )
        nucleus_table = self._parameters.nuclei.get(request.nucleus)
        if nucleus_table is None:
            available = ", ".join(sorted(self._parameters.nuclei)) or "none"
            raise Dp5qStubParameterError(
                f"no packaged DP5q parameters for nucleus {request.nucleus!r}; "
                f"available nuclei: {available}"
            )
        geometry = request.geometries[0]
        shifts: dict[str, PredictedShift] = {}
        for symbol, atom_uid in zip(geometry.symbols, geometry.atom_uids):
            entry = nucleus_table.get(symbol)
            if entry is None:
                continue  # not a target atom for this nucleus (e.g. H in a 13C request)
            distribution = (
                ShiftDistribution(mean_ppm=entry.mean_ppm, std_ppm=entry.std_ppm)
                if entry.std_ppm is not None
                else None
            )
            shifts[atom_uid] = PredictedShift(shift_ppm=entry.mean_ppm, distribution=distribution)
        if not shifts:
            geometry_elements = ", ".join(sorted(set(geometry.symbols))) or "none"
            table_elements = ", ".join(sorted(nucleus_table)) or "none"
            raise Dp5qStubParameterError(
                f"packaged DP5q parameters for nucleus {request.nucleus!r} cover no "
                f"requested atom element (geometry elements: {geometry_elements}; "
                f"available elements: {table_elements})"
            )
        return ShiftPrediction(
            nucleus=request.nucleus,
            model=self.model_provenance,
            geometry_requirements=self.geometry_requirements,
            shifts=shifts,
        )


def load_dp5q_stub_predictor(
    *,
    parameters_path: Path | None = None,
    geometry_requirements: GeometryRequirements | None = None,
) -> Dp5qStubPredictor:
    """Load the stub adapter from the optional runtime — explicit callers only.

    Loading never registers anything and never trains.  Packaged parameters
    are picked up when present; otherwise the returned adapter refuses to
    predict until parameters are supplied.

    Raises:
        Dp5qStubUnavailableError: The optional runtime is absent/broken (the
            typed status is carried on the error).
        Dp5qStubParameterError: Packaged parameters exist but are malformed.
    """
    resolved = _resolved_parameters_path(parameters_path)
    runtime, status = _inspect_optional_runtime(resolved)
    if runtime is None or not status.available:
        raise Dp5qStubUnavailableError(status)
    parameters = Dp5qStubParameters.load(resolved) if status.parameters_present else None
    logger.debug("loaded isolated DP5q stub adapter (parameters=%s)", parameters is not None)
    return Dp5qStubPredictor(
        runtime=runtime,
        parameters=parameters,
        geometry_requirements=geometry_requirements,
    )


def register_dp5q_stub(
    *,
    parameters_path: Path | None = None,
    geometry_requirements: GeometryRequirements | None = None,
    replace: bool = False,
) -> str:
    """Explicitly register the stub in the todo-55 registry; returns its model id.

    Nothing registers at import time and :func:`load_dp5q_stub_predictor`
    alone never registers either — this function is the only registration
    path, so the default NMR chain can never select the stub implicitly.

    Raises:
        Dp5qStubUnavailableError: The optional runtime is absent/broken.
        Dp5qStubParameterError: Packaged parameters exist but are malformed.
        DuplicateShiftPredictorError: The id is already registered and
            *replace* is False (delegated to the todo-55 registry).
    """
    predictor = load_dp5q_stub_predictor(
        parameters_path=parameters_path,
        geometry_requirements=geometry_requirements,
    )
    return register_shift_predictor(predictor, replace=replace)
