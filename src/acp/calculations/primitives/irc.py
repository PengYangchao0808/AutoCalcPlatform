"""Intrinsic reaction coordinate (IRC) calculation primitive.

Executes a forward+reverse IRC from a converged transition state, parses
endpoint geometries, materialises them under ``RESULT/irc/``, and registers
``IRC_ENDPOINT`` products in the result manifest.

This is a **standalone** request — not part of any ``CalculationPlan``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCResult, to_qc_result
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationResult,
    JsonValue,
    StructureArtifact,
    StructureRole,
)
from acp.storage.manifest import ProductKind, ResultManifest
from cccp.qc.interfaces.orca_ts import parse_irc_endpoints
from cccp.utils import file_io

from ._common import (
    CalculationInputs,
    backend_for_request,
    backend_name,
    error_text,
    load_inputs,
    output_dir,
    result_from_qc,
)

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)
_IRC_RESOURCE_KEYS = frozenset(
    {
        "backend",
        "engine",
        "config",
        "output_dir",
        "result_dir",
        "coordinates",
        "symbols",
        "charge",
        "multiplicity",
        "method",
    }
)


def run_irc(
    ts_artifact: StructureArtifact,
    *,
    directions: tuple[str, ...] = ("forward", "reverse"),
    method: str = "",
    resources: dict[str, Any] | None = None,
    workflow: str = "irc",
    profile: str | None = None,
) -> CalculationResult:
    """Run an IRC calculation from a converged transition state.

    Args:
        ts_artifact: Input structure **must** have ``role == TRANSITION_STATE``.
        directions: IRC directions to compute (``"forward"``, ``"reverse"``,
            or both).
        method: Override method string forwarded to the backend.
        resources: Additional resources (backend, charge, multiplicity, etc.).
        workflow: Workflow label for the result manifest.
        profile: Profile label for provenance.

    Returns:
        CalculationResult with endpoint artifacts and manifest registration.

    Raises:
        ValueError: If the input artifact role is not ``TRANSITION_STATE``.
    """
    if ts_artifact.role != StructureRole.TRANSITION_STATE:
        raise ValueError(
            f"IRC requires a transition-state artifact; got role={ts_artifact.role.value!r}"
        )

    resources = dict(resources or {})
    resources.setdefault("backend", "orca")
    if method:
        resources["method"] = method

    from acp.calculations.contracts import CalculationRequest

    request = CalculationRequest(
        input_artifact=ts_artifact,
        method=method,
        resources=resources,
        workflow=workflow,
        profile=profile,
    )

    inputs = load_inputs(request)
    selected_backend = backend_name(request)
    backend = backend_for_request(request, selected_backend)
    target_dir = output_dir(request) or Path.cwd() / "irc_work"
    target_dir.mkdir(parents=True, exist_ok=True)

    direction_str = _resolve_direction(directions)

    # --- Backend call ---
    try:
        raw_result = backend.irc(
            inputs.coordinates,
            list(inputs.symbols),
            charge=inputs.charge,
            multiplicity=inputs.multiplicity,
            output_dir=target_dir,
            direction=direction_str,
            **_irc_kwargs(request),
        )
    except _BACKEND_FAILURES as error:
        return result_from_qc(
            request,
            selected_backend,
            None,
            [error_text(error)],
            [],
            metadata=_irc_metadata(directions),
            status="failed",
        )

    # Normalise to QCResult
    qc_result = to_qc_result(raw_result) if not isinstance(raw_result, QCResult) else raw_result

    # --- Parse endpoint geometries ---
    endpoints = _discover_endpoints(raw_result, target_dir, inputs)
    success = bool(getattr(raw_result, "success", False)) or bool(qc_result.success)
    errors: list[str] = []
    if not success:
        raw_error = getattr(raw_result, "error_message", None) or qc_result.error_message
        if raw_error:
            errors.append(str(raw_error))

    # --- Materialise endpoint structures ---
    result_dir = _result_dir(request, target_dir)
    artifacts = _write_endpoint_products(
        request,
        selected_backend,
        result_dir,
        endpoints,
        inputs,
        success,
    )

    metadata = _irc_metadata(directions)
    metadata["endpoint_count"] = len(endpoints)
    for direction in ("forward", "reverse"):
        key = f"{direction}_endpoint"
        if direction in endpoints:
            metadata[key] = str(endpoints[direction]["path"])

    return result_from_qc(
        request,
        selected_backend,
        qc_result,
        errors,
        artifacts,
        metadata=metadata,
        status="completed" if success else "failed",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_direction(directions: tuple[str, ...]) -> str:
    """Map a directions tuple to the ORCA direction keyword."""
    forward = "forward" in directions
    reverse = "reverse" in directions
    if forward and reverse:
        return "both"
    if forward:
        return "forward"
    if reverse:
        return "reverse"
    return "both"


def _irc_kwargs(request: Any) -> dict[str, Any]:
    """Forward backend-specific IRC options without leaking request internals."""
    kwargs = {
        key: value for key, value in request.resources.items() if key not in _IRC_RESOURCE_KEYS
    }
    if request.method:
        kwargs["method"] = request.method
    return kwargs


def _irc_metadata(directions: tuple[str, ...]) -> dict[str, JsonValue]:
    return {"directions": list(directions)}


def _result_dir(request: Any, target_dir: Path) -> Path:
    """Resolve the task RESULT directory from an explicit path or WORK layout."""
    raw_result_dir = request.resources.get("result_dir")
    if isinstance(raw_result_dir, str) and raw_result_dir:
        return Path(raw_result_dir)
    for parent in (target_dir, *target_dir.parents):
        if parent.name == "RESULT":
            return parent
        if parent.name == "WORK":
            return parent.parent / "RESULT"
    return target_dir / "RESULT"


def _discover_endpoints(
    raw_result: object,
    target_dir: Path,
    inputs: CalculationInputs,
) -> dict[str, dict[str, Any]]:
    """Extract endpoint geometries from the backend result or discover files.

    Returns a dict mapping direction → {"path": Path, "coordinates": NDArray, "symbols": list[str]}.
    """
    endpoints: dict[str, dict[str, Any]] = {}

    # 1. Try explicit endpoint paths from the result (IrcResult-style)
    raw_endpoints = getattr(raw_result, "endpoints", None)
    if isinstance(raw_endpoints, dict):
        for direction, path_value in raw_endpoints.items():
            if direction in ("forward", "reverse") and path_value is not None:
                path = Path(path_value)
                if path.exists():
                    coords, symbols = _read_xyz(path)
                    if coords is not None:
                        endpoints[direction] = {
                            "path": path,
                            "coordinates": coords,
                            "symbols": symbols,
                        }

    # 2. Try final_geometries from the result
    final_geometries = getattr(raw_result, "final_geometries", None)
    if isinstance(final_geometries, dict):
        for direction, geometry in final_geometries.items():
            if direction in endpoints or direction not in ("forward", "reverse"):
                continue
            if geometry is not None:
                coords = np.asarray(geometry, dtype=float)
                if coords.ndim == 2 and coords.shape[1] == 3:
                    # Write a temporary endpoint file
                    endpoint_path = target_dir / f"irc_{direction[0]}.xyz"
                    file_io.write_xyz(
                        endpoint_path,
                        coords,
                        list(inputs.symbols),
                        title=f"IRC {direction} endpoint",
                    )
                    endpoints[direction] = {
                        "path": endpoint_path,
                        "coordinates": coords,
                        "symbols": list(inputs.symbols),
                    }

    # 3. Fallback: discover endpoint files via parse_irc_endpoints
    if not endpoints:
        discovered = parse_irc_endpoints("", target_dir)
        for direction, path in discovered.items():
            if direction in endpoints:
                continue
            coords, symbols = _read_xyz(path)
            if coords is not None:
                endpoints[direction] = {"path": path, "coordinates": coords, "symbols": symbols}

    return endpoints


def _read_xyz(path: Path) -> tuple[NDArray[np.float64] | None, list[str]]:
    """Read an XYZ file and return (coordinates, symbols)."""
    try:
        coordinates, symbols = file_io.read_xyz(path)
        return np.asarray(coordinates, dtype=float), [str(s) for s in symbols]
    except (FileNotFoundError, ValueError, OSError):
        return None, []


def _write_endpoint_products(
    request: Any,
    backend: str,
    result_dir: Path,
    endpoints: dict[str, dict[str, Any]],
    inputs: CalculationInputs,
    success: bool,
) -> list[ArtifactRef]:
    """Materialise endpoint XYZ files under ``RESULT/irc/`` and register products."""
    irc_dir = result_dir / "irc"
    irc_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[ArtifactRef] = []
    manifest = ResultManifest(
        task_id="",
        workflow=request.workflow or "irc",
        status="completed" if success else "failed",
    )

    for direction in ("forward", "reverse"):
        if direction not in endpoints:
            continue
        info = endpoints[direction]
        coords = info["coordinates"]
        symbols = info.get("symbols") or list(inputs.symbols)
        out_path = irc_dir / f"irc_{direction}.xyz"

        file_io.write_xyz(
            out_path,
            np.asarray(coords, dtype=float),
            list(symbols),
            title=f"IRC {direction} endpoint",
        )

        relative_path = str(out_path.relative_to(result_dir))
        artifacts.append(ArtifactRef(path=out_path, type="structure", source=backend))
        _ = manifest.add_product(
            id=f"irc_{direction}_endpoint",
            label=f"IRC {direction} endpoint",
            path=relative_path,
            kind=ProductKind.IRC_ENDPOINT,
        )

    _ = manifest.write(result_dir)
    return artifacts


__all__ = ["run_irc"]
