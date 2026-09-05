# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnnecessaryIsInstance=false
"""Shared input, backend, and result plumbing for calculation primitives."""

from __future__ import annotations

import hashlib
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
    JsonValue,
    Provenance,
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
    }
)


@dataclass(frozen=True, slots=True)
class CalculationInputs:
    """Parsed geometry and electronic-state values sent to a capability."""

    coordinates: NDArray[np.float64]
    symbols: tuple[str, ...]
    charge: int
    multiplicity: int


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

    return CalculationInputs(
        coordinates=normalized_coordinates,
        symbols=symbols,
        charge=_resource_int(request, "charge", 0),
        multiplicity=_resource_int(request, "multiplicity", 1),
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
    """Call one capability and normalize its legacy or standard result."""
    operation = getattr(backend, capability)
    raw_result = operation(
        inputs.coordinates,
        list(inputs.symbols),
        charge=inputs.charge,
        multiplicity=inputs.multiplicity,
        output_dir=target_dir,
        **dict(kwargs),
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
    "backend_for_request",
    "backend_name",
    "call_capability",
    "capability_kwargs",
    "error_text",
    "load_inputs",
    "output_dir",
    "result_from_qc",
]
