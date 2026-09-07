# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnnecessaryIsInstance=false
"""Shared input, backend, and result plumbing for calculation primitives."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

import acp.backends
from acp.backends.base import QCResult, to_qc_result
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    ElectronicStateConfig,
    ElectronicStateSpec,
    GuessStrategy,
    JsonValue,
    Provenance,
    SpatialSymmetryMode,
    SpinMode,
    StabilityMode,
    WavefunctionSource,
    electron_parity_ok,
    electronic_state_config_from_dict,
    expected_s2_for_multiplicity,
    orca_xyz_multiplicity,
    validate_electronic_state,
)
from acp.io.structures import StructureReader

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

_SYMBOL_TO_Z = {
    "H": 1, "HE": 2, "LI": 3, "BE": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9,
    "NE": 10, "NA": 11, "MG": 12, "AL": 13, "SI": 14, "P": 15, "S": 16, "CL": 17,
    "AR": 18, "K": 19, "CA": 20, "SC": 21, "TI": 22, "V": 23, "CR": 24, "MN": 25,
    "FE": 26, "CO": 27, "NI": 28, "CU": 29, "ZN": 30, "GA": 31, "GE": 32, "AS": 33,
    "SE": 34, "BR": 35, "KR": 36, "RB": 37, "SR": 38, "Y": 39, "ZR": 40, "NB": 41,
    "MO": 42, "TC": 43, "RU": 44, "RH": 45, "PD": 46, "AG": 47, "CD": 48, "IN": 49,
    "SN": 50, "SB": 51, "TE": 52, "I": 53, "XE": 54, "CS": 55, "BA": 56, "LA": 57,
    "W": 74, "PT": 78, "AU": 79, "HG": 80, "PB": 82, "BI": 83,
}


def _electron_count(symbols: tuple[str, ...], charge: int) -> int | None:
    return electron_count(symbols, charge)


def electron_count(symbols: tuple[str, ...], charge: int) -> int | None:
    """Total electron count from element symbols, or ``None`` if unknown."""
    total = 0
    for symbol in symbols:
        z_value = _SYMBOL_TO_Z.get(symbol.strip().upper())
        if z_value is None:
            return None
        total += z_value
    return total - charge


@dataclass(frozen=True, slots=True)
class CalculationInputs:
    """Parsed geometry and electronic-state values sent to a capability."""

    coordinates: NDArray[np.float64]
    symbols: tuple[str, ...]
    charge: int
    multiplicity: int
    scf_options: dict[str, Any] | None = None
    electronic_state: ElectronicStateSpec | None = None
    electronic_state_config: ElectronicStateConfig | None = None


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
    selected state to ORCA-flavoured ``scf_options`` (§9). Only the ORCA
    backend receives spin-control options; other backends keep the
    reference/target multiplicity only.

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
    n_electrons = _electron_count(symbols, charge)
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

    scf_options = _build_scf_options(state, request)
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


def _build_scf_options(state: ElectronicStateSpec, request: CalculationRequest) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if state.spin_mode is SpinMode.RESTRICTED:
        options["hf_typ"] = "RHF"
    elif state.spin_mode in (SpinMode.UNRESTRICTED, SpinMode.BROKEN_SYMMETRY):
        options["hf_typ"] = "UHF"

    guess = state.guess
    if guess.strategy is GuessStrategy.GUESSMIX:
        options.setdefault("hf_typ", "UHF")
        options["guess_mix_angle"] = guess.guess_mix_angle
    elif guess.strategy is GuessStrategy.FLIPSPIN:
        options["hf_typ"] = "UHF"
        options["flip_spin_atoms"] = list(guess.orca_flip_atoms())
        if guess.final_ms is not None:
            options["final_ms"] = guess.final_ms
    elif guess.strategy is GuessStrategy.BROKEN_SYM:
        options.setdefault("hf_typ", "UHF")
        if guess.broken_sym_na is not None and guess.broken_sym_nb is not None:
            options["broken_sym_na"] = guess.broken_sym_na
            options["broken_sym_nb"] = guess.broken_sym_nb
    elif guess.strategy in (GuessStrategy.MOREAD, GuessStrategy.STABILITY_RESTART):
        source = guess.orbital_source or (
            state.wavefunction.artifact_path
            if state.wavefunction.source is WavefunctionSource.ARTIFACT
            else None
        )
        if source is not None:
            options["mo_read_path"] = str(source)

    if state.wavefunction.source is WavefunctionSource.ARTIFACT and "mo_read_path" not in options:
        if state.wavefunction.artifact_path is not None:
            options["mo_read_path"] = str(state.wavefunction.artifact_path)

    if state.spatial_symmetry is SpatialSymmetryMode.DISABLE:
        options["no_use_sym"] = True
    elif state.spatial_symmetry is SpatialSymmetryMode.PRESERVE:
        options["use_sym"] = True

    if state.diagnostics.write_spin_density:
        options["write_spin_density"] = True
    return options


def backend_name(request: CalculationRequest) -> str:
    """Resolve the requested backend, defaulting to ORCA."""
    raw_name = request.resources.get("backend", request.resources.get("engine", "orca"))
    name = str(raw_name).lower()
    if name not in _BACKEND_NAMES:
        known = ", ".join(sorted(_BACKEND_NAMES))
        raise ValueError(f"unsupported calculation backend {name!r}; expected {known}")
    return name


def load_inputs(request: CalculationRequest) -> CalculationInputs:
    """Load coordinates from request resources or the input structure artifact."""
    raw_coordinates = request.resources.get("coordinates")
    raw_symbols = request.resources.get("symbols")

    if raw_coordinates is None:
        structure = StructureReader().read(request.input_artifact.path)
        coordinates = structure.coordinates
        parsed_symbols = tuple(structure.symbols)
        if coordinates is None:
            raise ValueError(f"input artifact has no coordinates: {request.input_artifact.path}")
    else:
        coordinates = np.asarray(raw_coordinates, dtype=np.float64)
        parsed_symbols = (
            tuple(value for value in raw_symbols if isinstance(value, str))
            if isinstance(raw_symbols, list)
            else ()
        )

    normalized_coordinates = np.asarray(coordinates, dtype=np.float64)
    if normalized_coordinates.ndim != 2 or normalized_coordinates.shape[1] != 3:
        raise ValueError("calculation coordinates must have shape (N, 3)")

    symbols = tuple(request.input_artifact.elements) or parsed_symbols
    if not symbols or len(symbols) != normalized_coordinates.shape[0]:
        raise ValueError("calculation coordinates and element symbols must have equal length")

    charge = _resource_int(request, "charge", 0)
    multiplicity = _resource_int(request, "multiplicity", 1)

    resolved_state = resolve_electronic_state(request, symbols, charge)
    if resolved_state is not None:
        if not electron_parity_ok(
            _electron_count(symbols, charge) or 0, resolved_state.state.target_multiplicity
        ) and _electron_count(symbols, charge) is not None:
            message = (
                f"electronic state {resolved_state.state.state_id!r} multiplicity "
                f"{resolved_state.state.target_multiplicity} is incompatible with the "
                "electron count"
            )
            raise ValueError(message)
        multiplicity = orca_xyz_multiplicity(resolved_state.state)

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
        electronic_state_config=resolved_state.config if resolved_state is not None else None,
    )


def output_dir(request: CalculationRequest) -> Path | None:
    """Return the optional capability output directory from request resources."""
    raw_output = request.resources.get("output_dir")
    if isinstance(raw_output, (str, Path)) and str(raw_output):
        return Path(raw_output)
    return None


def backend_for_request(request: CalculationRequest, name: str) -> Any:
    """Resolve a backend instance while preserving the registry seam for tests."""
    backend_ref = acp.backends.get_backend(name)
    if isinstance(backend_ref, type):
        constructor_kwargs = _constructor_kwargs(request, name)
        return backend_ref(_backend_config(request), **constructor_kwargs)
    return backend_ref


def capability_kwargs(request: CalculationRequest) -> dict[str, Any]:
    """Build capability keyword arguments from request resources."""
    kwargs = {key: value for key, value in request.resources.items() if key not in _RESOURCE_KEYS}
    if request.method:
        kwargs["method"] = request.method
    return kwargs


def call_capability(
    backend: Any,
    capability: str,
    inputs: CalculationInputs,
    target_dir: Path | None,
    kwargs: Mapping[str, Any],
) -> QCResult:
    """Call one capability and normalize its legacy or standard result.

    Stability analysis is restricted to SP-like nodes (§3.4, §13.4): an
    OPT/FREQ request never receives ``STABPerform`` — the plan executor
    appends a dedicated SP diagnostic node instead.
    """
    operation = getattr(backend, capability)
    final_kwargs = dict(kwargs)
    if inputs.scf_options:
        options = dict(inputs.scf_options)
        if capability == "single_point" and inputs.electronic_state is not None:
            if inputs.electronic_state.diagnostics.stability is not StabilityMode.NONE:
                options.setdefault("stab_perform", True)
                options.setdefault("stab_restart", True)
        if options:
            final_kwargs["scf_options"] = options
    raw_result = operation(
        inputs.coordinates,
        list(inputs.symbols),
        charge=inputs.charge,
        multiplicity=inputs.multiplicity,
        output_dir=target_dir,
        **final_kwargs,
    )
    return to_qc_result(raw_result)


def result_from_qc(
    request: CalculationRequest,
    backend: str,
    qc_result: QCResult | None,
    errors: list[str],
    artifacts: list[ArtifactRef],
    metadata: Mapping[str, JsonValue] | None = None,
    status: str | None = None,
) -> CalculationResult:
    """Convert a normalized QC result into the calculation contract."""
    qc_metadata = _json_mapping(qc_result.metadata if qc_result is not None else {})
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


def artifacts_from_qc(
    qc_result: QCResult,
    backend: str,
    existing: list[ArtifactRef] | None = None,
) -> list[ArtifactRef]:
    """Collect file references exposed by a QC result without requiring files."""
    artifacts = list(existing or [])
    known_paths = {artifact.path for artifact in artifacts}
    for field_name, artifact_type in (
        ("output_file", "output"),
        ("log_file", "log"),
        ("freq_log_file", "frequency_log"),
    ):
        raw_path = getattr(qc_result, field_name, None)
        if not isinstance(raw_path, (str, Path)) or not str(raw_path):
            continue
        path = Path(raw_path)
        if path in known_paths:
            continue
        artifacts.append(
            ArtifactRef(
                path=path,
                type=artifact_type,
                checksum=_checksum(path),
                source=backend,
            )
        )
        known_paths.add(path)
    return artifacts


def error_text(error: BaseException) -> str:
    """Return a stable non-empty message for a backend exception."""
    message = str(error).strip()
    return message or type(error).__name__


# ── electronic-state quality gate and metadata (§10.3, §12.1) ───────────


def _spin_centers(
    populations: Mapping[str, Any] | Mapping[int, float], threshold: float
) -> tuple[list[int], list[int]]:
    positive: list[int] = []
    negative: list[int] = []
    for raw_index, value in populations.items():
        try:
            atom_index = int(raw_index)
            spin_value = float(value)
        except (TypeError, ValueError):
            continue
        if spin_value >= threshold:
            positive.append(atom_index)
        elif spin_value <= -threshold:
            negative.append(atom_index)
    return sorted(positive), sorted(negative)


def assess_state_quality(
    state: ElectronicStateSpec,
    diagnostics: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """Three-state collapse verdict: accepted | warning | collapsed (§10.3).

    ``<S²>`` alone is never a hard diradical criterion; the verdict combines
    the gate window with the sign structure of the atomic spin populations.
    """
    verdict = "accepted"
    notes: list[str] = []
    gate = state.quality_gate
    s2 = diagnostics.get("s2")
    raw_populations = diagnostics.get("mulliken_spin_populations") or diagnostics.get(
        "loewdin_spin_populations"
    )
    populations = raw_populations if isinstance(raw_populations, Mapping) else {}
    positive, negative = _spin_centers(populations, gate.spin_center_threshold)
    is_broken_symmetry = state.spin_mode is SpinMode.BROKEN_SYMMETRY

    collapsed = False
    if is_broken_symmetry:
        if populations and not positive and not negative:
            collapsed = True
            notes.append(
                "no significant local spin density: alpha/beta densities are identical"
            )
        if s2 is not None and gate.s2_min is not None and s2 < gate.s2_min:
            if not positive or not negative:
                collapsed = True
            notes.append(f"<S^2>={s2:.4f} below gate s2_min={gate.s2_min:g}")

    if collapsed:
        return "collapsed", notes

    if is_broken_symmetry:
        if gate.require_opposite_spin_centers and populations and not (positive and negative):
            verdict = "warning"
            notes.append("opposite-sign spin centers required but not observed")
        if s2 is not None and gate.s2_max is not None and s2 > gate.s2_max:
            verdict = "warning"
            notes.append(f"<S^2>={s2:.4f} above gate s2_max={gate.s2_max:g}")
        if s2 is not None and gate.s2_min is not None and s2 < gate.s2_min:
            verdict = "warning"
    return verdict, notes


def electronic_state_result_metadata(
    inputs: CalculationInputs,
    qc_result: QCResult | None,
) -> tuple[dict[str, JsonValue], list[str], str | None]:
    """Build the §12.1 ``electronic_state`` metadata block and gate outcome.

    Returns:
        ``(metadata, errors, forced_status)`` — ``forced_status`` is
        ``"failed"`` when the collapse policy escalates to error.
    """
    state = inputs.electronic_state
    if state is None:
        return {}, [], None

    raw_diagnostics = (
        qc_result.metadata.get("electronic_state_diagnostics") if qc_result else None
    )
    diagnostics = raw_diagnostics if isinstance(raw_diagnostics, Mapping) else {}

    verdict, notes = assess_state_quality(state, diagnostics)
    metadata: dict[str, JsonValue] = {
        "state_id": state.state_id,
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
        "state_status": verdict,
    }
    s2_value = diagnostics.get("s2")
    if isinstance(s2_value, (int, float)):
        metadata["s2"] = float(s2_value)
    raw_populations = diagnostics.get("mulliken_spin_populations") or diagnostics.get(
        "loewdin_spin_populations"
    )
    if isinstance(raw_populations, Mapping):
        positive, negative = _spin_centers(
            raw_populations, state.quality_gate.spin_center_threshold
        )
        metadata["positive_spin_centers"] = positive
        metadata["negative_spin_centers"] = negative

    errors: list[str] = []
    forced_status: str | None = None
    if verdict == "collapsed":
        collapse_message = (
            f"electronic state {state.state_id!r} collapsed to a "
            + "restricted-like solution: "
            + "; ".join(notes)
        )
        if state.quality_gate.collapse_policy.value == "error":
            errors.append(collapse_message)
            forced_status = "failed"
        elif state.quality_gate.collapse_policy.value == "warning":
            metadata["state_notes"] = notes
            logger.warning("%s", collapse_message)
        else:
            metadata["state_notes"] = notes
    elif notes:
        metadata["state_notes"] = notes
    return metadata, errors, forced_status


def write_state_artifacts(
    inputs: CalculationInputs,
    qc_result: QCResult | None,
    target_dir: Path | None,
    backend: str,
) -> list[ArtifactRef]:
    """Persist ``electronic_state.json`` / ``spin_diagnostics.json`` (§12.3)."""
    state = inputs.electronic_state
    if state is None or target_dir is None:
        return []

    artifacts: list[ArtifactRef] = []
    raw_diagnostics = (
        qc_result.metadata.get("electronic_state_diagnostics") if qc_result else None
    )
    diagnostics = raw_diagnostics if isinstance(raw_diagnostics, Mapping) else {}
    metadata, _, _ = electronic_state_result_metadata(inputs, qc_result)

    state_payload: dict[str, Any] = dict(metadata)
    state_payload["quality_gate"] = {
        "collapse_policy": state.quality_gate.collapse_policy.value,
        "s2_min": state.quality_gate.s2_min,
        "s2_max": state.quality_gate.s2_max,
        "require_opposite_spin_centers": state.quality_gate.require_opposite_spin_centers,
    }
    state_file = target_dir / "electronic_state.json"
    _write_json_artifact(state_file, state_payload)
    artifacts.append(_artifact_ref(state_file, "electronic_state", backend))

    if diagnostics:
        diagnostics_file = target_dir / "spin_diagnostics.json"
        _write_json_artifact(diagnostics_file, dict(diagnostics))
        artifacts.append(_artifact_ref(diagnostics_file, "spin_diagnostics", backend))

    if state.wavefunction.inherit_between_steps:
        for candidate in sorted(target_dir.glob("*.gbw")):
            artifacts.append(_artifact_ref(candidate, "wavefunction", backend))
            break
    return artifacts


def _write_json_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("failed to write %s: %s", path, exc)


def _artifact_ref(path: Path, artifact_type: str, backend: str) -> ArtifactRef:
    return ArtifactRef(
        path=path,
        type=artifact_type,
        checksum=_checksum(path),
        source=backend,
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


def _checksum(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _json_mapping(values: Mapping[str, Any]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in values.items():
        parsed = _json_value(value)
        if parsed is not None or value is None:
            result[str(key)] = parsed
    return result


def _json_value(value: Any) -> JsonValue | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        parsed_items = [_json_value(item) for item in value]
        return [item for item in parsed_items if item is not None]
    if isinstance(value, Mapping):
        return _json_mapping(value)
    return None


__all__ = [
    "CalculationInputs",
    "artifacts_from_qc",
    "assess_state_quality",
    "backend_for_request",
    "backend_name",
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
