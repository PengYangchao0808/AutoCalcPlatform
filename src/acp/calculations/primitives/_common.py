# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnnecessaryIsInstance=false
"""Compat delegation for calculation primitives (migration station).

Since plan todo 17 the shared scientific pipeline lives in
:mod:`cccp.calculation._common` (geometry loading via
``MolecularInputHandler``, electronic-state scf options, quality gate, state
artifacts, capability dispatch, QC artifact collection).  This module keeps
only the legacy-shape surface still consumed by the not-yet-migrated
primitives (optimize/frequency/scan/irc/casscf) and by characterization
tests: ``CalculationRequest`` resource parsing, the ACP ``CalculationResult``
envelope mapping, and thin adapters onto the cccp pipeline.  No second
implementation body lives here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp.calculations.contracts import (
    CalculationRequest,
    CalculationResult,
    ElectronicStateConfig,
    Provenance,
    electronic_state_config_from_dict,
    validate_electronic_state,
)
from cccp.calculation._common import (
    CalculationInputs,
    apply_stability_options,
    artifacts_from_qc,
    assess_state_quality,
    build_state_scf_options,
    call_capability,
    electron_count,
    error_text,
    load_geometry,
    qc_metadata_json,
    resolve_multiplicity,
    state_result_metadata,
)
from cccp.calculation._common import (
    backend_for_request as _cccp_backend_for_request,
)
from cccp.calculation._common import (
    write_state_artifacts as _cccp_write_state_artifacts,
)
from cccp.calculation.contracts import (
    ArtifactRef,
    ElectronicStateSpec,
    GuessStrategy,
    JsonValue,
    StabilityMode,
    expected_s2_for_multiplicity,
)

logger = logging.getLogger(__name__)

_BACKEND_NAMES = frozenset({"orca", "xtb"})
_RESOURCE_KEYS = frozenset(
    {
        "backend",
        "engine",
        "config",
        "output_dir",
        "coordinates",
        "symbols",
        "charge",
        "multiplicity",
        "failure_type",
        "structure_kind",
        "trajectory_item_id",
        "electronic_state",
        "stability_check",
    }
)

#: SCF level keys lifted into the typed ``MethodSpec.scf`` home by
#: ``legacy_adapters.to_task_request``.  The ORCA OptTS capability turns a
#: residual ``scf`` kwarg into a raw route token (a second, unvalidated
#: spelling); the typed spec already renders the governed keyword, so keep
#: these off the passthrough extras.
_LEVEL_RESOURCE_KEYS = frozenset({"scf", "scf_convergence"})


def _electron_count(symbols: tuple[str, ...], charge: int) -> int | None:
    return electron_count(symbols, charge)


@dataclass(frozen=True, slots=True)
class _ResolvedState:
    state: ElectronicStateSpec
    config: ElectronicStateConfig
    scf_options: dict[str, Any]
    stability_requested: bool
    metadata: dict[str, JsonValue]


def resolve_electronic_state(
    request: CalculationRequest,
    symbols: tuple[str, ...],
    charge: int,
) -> _ResolvedState | None:
    """Resolve the ``electronic_state`` resource into backend-ready options.

    Performs contract validation (§13) at the trust boundary and maps the
    selected state to ORCA-flavoured ``scf_options`` (§9) via the shared cccp
    pipeline.  Only the ORCA backend receives spin-control options; other
    backends keep the reference/target multiplicity only.

    Raises:
        ValueError: On contract violations or a state-sweep handed to a
            single-request primitive (the batch engine must pre-expand).
    """
    raw_state = request.resources.get("electronic_state")
    if not isinstance(raw_state, Mapping) or not raw_state:
        return None

    config = electronic_state_config_from_dict(raw_state)
    if not config.states:
        return None
    if config.execution_mode.value == "state_sweep":
        message = (
            "state_sweep electronic_state must be expanded by the batch "
            "engine before reaching a single-request primitive"
        )
        raise ValueError(message)

    state = config.selected_state()
    if state is None:
        return None

    backend = str(
        request.resources.get("backend", request.resources.get("engine", "orca"))
    ).lower()
    n_atoms = len(symbols)
    n_electrons = electron_count(symbols, charge)
    validation = validate_electronic_state(
        config,
        backend=backend,
        n_electrons=n_electrons,
        n_atoms=n_atoms,
    )
    if validation.errors:
        message = "electronic_state validation failed: " + "; ".join(validation.errors)
        raise ValueError(message)
    for warning in validation.warnings:
        logger.warning("electronic_state: %s", warning)

    scf_options = build_state_scf_options(state)
    bootstrap = request.resources.get("electronic_state", {})
    if isinstance(bootstrap, Mapping):
        bootstrap_path = bootstrap.get("wavefunction_bootstrap")
        if isinstance(bootstrap_path, str) and bootstrap_path:
            scf_options.setdefault("mo_read_path", bootstrap_path)
    stability_requested = bool(request.resources.get("stability_check")) or (
        state.diagnostics.stability is not StabilityMode.NONE
    )

    metadata: dict[str, JsonValue] = {
        "state_id": state.state_id,
        "label": state.label,
        "spin_mode": state.spin_mode.value,
        "target_multiplicity": state.target_multiplicity,
        "reference_multiplicity": state.guess.reference_multiplicity
        if state.guess.strategy is GuessStrategy.FLIPSPIN
        else None,
        "final_ms": (
            state.guess.final_ms if state.guess.strategy is GuessStrategy.FLIPSPIN else None
        ),
        "guess_strategy": state.guess.strategy.value,
        "expected_s2": expected_s2_for_multiplicity(state.target_multiplicity),
    }
    return _ResolvedState(
        state=state,
        config=config,
        scf_options=scf_options,
        stability_requested=stability_requested,
        metadata=metadata,
    )


def backend_name(request: CalculationRequest) -> str:
    """Resolve the requested backend, defaulting to ORCA."""
    raw_name = request.resources.get("backend", request.resources.get("engine", "orca"))
    name = str(raw_name).lower()
    if name not in _BACKEND_NAMES:
        known = ", ".join(sorted(_BACKEND_NAMES))
        raise ValueError(f"unsupported calculation backend {name!r}; expected {known}")
    return name


def load_inputs(request: CalculationRequest) -> CalculationInputs:
    """Load coordinates from request resources or the input structure artifact.

    Geometry loading is delegated to ``cccp.io.input_handler`` (never
    ``acp.io``); inline geometry overrides the file (legacy precedence).
    """
    raw_coordinates = request.resources.get("coordinates")
    raw_symbols = request.resources.get("symbols")

    if raw_coordinates is None:
        coordinates, parsed_symbols = load_geometry(
            path=request.input_artifact.path,
            elements=(),
        )
    else:
        symbols_tuple = (
            tuple(value for value in raw_symbols if isinstance(value, str))
            if isinstance(raw_symbols, list)
            else ()
        )
        coordinates, parsed_symbols = load_geometry(
            coordinates=raw_coordinates,
            symbols=symbols_tuple,
        )

    normalized_coordinates = coordinates
    symbols = tuple(request.input_artifact.elements) or parsed_symbols
    if not symbols or len(symbols) != normalized_coordinates.shape[0]:
        raise ValueError("calculation coordinates and element symbols must have equal length")

    charge = _resource_int(request, "charge", 0)
    multiplicity = _resource_int(request, "multiplicity", 1)

    resolved_state = resolve_electronic_state(request, symbols, charge)
    if resolved_state is not None:
        multiplicity = resolve_multiplicity(
            resolved_state.state, symbols, charge, multiplicity
        )

    backend = str(
        request.resources.get("backend", request.resources.get("engine", "orca"))
    ).lower()
    scf_options = (
        resolved_state.scf_options
        if resolved_state is not None and backend == "orca"
        else None
    )

    return CalculationInputs(
        coordinates=normalized_coordinates,
        symbols=symbols,
        charge=charge,
        multiplicity=multiplicity,
        scf_options=scf_options,
        electronic_state=resolved_state.state if resolved_state is not None else None,
    )


def output_dir(request: CalculationRequest) -> Path | None:
    """Return the optional capability output directory from request resources."""
    raw_output = request.resources.get("output_dir")
    if isinstance(raw_output, (str, Path)) and str(raw_output):
        return Path(raw_output)
    return None


def backend_for_request(request: CalculationRequest, name: str) -> Any:
    """Resolve a backend instance through the shared cccp registry seam."""
    return _cccp_backend_for_request(
        name,
        config=_backend_config(request),
        constructor_kwargs=_constructor_kwargs(request, name),
    )


def capability_kwargs(request: CalculationRequest) -> dict[str, Any]:
    """Build capability keyword arguments from request resources."""
    kwargs = {
        key: value
        for key, value in request.resources.items()
        if key not in _RESOURCE_KEYS and key not in _LEVEL_RESOURCE_KEYS
    }
    if request.method:
        kwargs["method"] = request.method
    return kwargs


def electronic_state_result_metadata(
    inputs: CalculationInputs,
    qc_result: Any,
) -> tuple[dict[str, JsonValue], list[str], str | None]:
    """Adapter onto the shared cccp §12.1 metadata gate (legacy signature)."""
    state = inputs.electronic_state
    if state is None:
        return {}, [], None
    raw = getattr(qc_result, "metadata", None) if qc_result is not None else None
    diagnostics = raw.get("electronic_state_diagnostics") if isinstance(raw, Mapping) else None
    return state_result_metadata(
        state, diagnostics if isinstance(diagnostics, Mapping) else {}
    )


def write_state_artifacts(
    inputs: CalculationInputs,
    qc_result: Any,
    target_dir: Path | None,
    backend: str,
) -> list[ArtifactRef]:
    """Adapter onto the shared cccp §12.3 artifact writer (legacy signature)."""
    state = inputs.electronic_state
    if state is None:
        return []
    raw = getattr(qc_result, "metadata", None) if qc_result is not None else None
    diagnostics = raw.get("electronic_state_diagnostics") if isinstance(raw, Mapping) else None
    return _cccp_write_state_artifacts(
        state, diagnostics if isinstance(diagnostics, Mapping) else {}, target_dir, backend
    )


def result_from_qc(
    request: CalculationRequest,
    backend: str,
    qc_result: Any,
    errors: list[str],
    artifacts: list[ArtifactRef],
    metadata: Mapping[str, JsonValue] | None = None,
    status: str | None = None,
) -> CalculationResult:
    """Convert a normalized QC result into the legacy calculation contract."""
    qc_metadata = qc_metadata_json(qc_result.metadata if qc_result is not None else {})
    if metadata:
        qc_metadata.update(metadata)
    if qc_result is None:
        return CalculationResult(
            artifacts=artifacts,
            status="failed",
            errors=list(errors),
            provenance=_provenance(request, backend),
            metadata=qc_metadata,
        )
    coordinates = (
        [[float(value) for value in row] for row in qc_result.coordinates]
        if qc_result.coordinates is not None
        else None
    )
    return CalculationResult(
        energy=qc_result.energy,
        coords=coordinates,
        frequencies=[float(value) for value in qc_result.frequencies or []],
        artifacts=artifacts,
        status=status or ("completed" if qc_result.success else "failed"),
        errors=list(errors),
        provenance=_provenance(request, backend),
        metadata=qc_metadata,
    )


def _resource_int(request: CalculationRequest, key: str, default: int) -> int:
    value = request.resources.get(key)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _backend_config(request: CalculationRequest) -> dict[str, Any]:
    raw_config = request.resources.get("config")
    if isinstance(raw_config, Mapping):
        return dict(raw_config)
    return {}


def _constructor_kwargs(request: CalculationRequest, backend: str) -> dict[str, Any]:
    kwargs = {key: value for key, value in request.resources.items() if key not in _RESOURCE_KEYS}
    if backend == "orca" and request.method:
        kwargs["method"] = request.method
    return kwargs


def _provenance(request: CalculationRequest, backend: str) -> Provenance:
    return Provenance(
        backend=backend,
        method=request.method,
        profile=request.profile or "default",
        version="unknown",
        input_signature=str(request.input_artifact.path),
    )


__all__ = [
    "CalculationInputs",
    "artifacts_from_qc",
    "assess_state_quality",
    "apply_stability_options",
    "backend_for_request",
    "backend_name",
    "build_state_scf_options",
    "call_capability",
    "capability_kwargs",
    "electron_count",
    "electronic_state_result_metadata",
    "error_text",
    "load_inputs",
    "output_dir",
    "resolve_electronic_state",
    "result_from_qc",
    "write_state_artifacts",
]
