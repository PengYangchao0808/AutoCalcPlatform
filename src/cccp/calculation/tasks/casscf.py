"""CASSCF / NEVPT2 task core (plan todo 22 — task 6).

Pipeline mirrors the legacy ACP ``run_casscf`` semantics exactly: ORCA-only
dispatch (another backend yields a structured failed result), active-space
spec validation via :func:`~cccp.calculation.contracts.validate_casscf_spec`
(failures are structured failed results, never subprocess launches), one
``casscf`` capability call, and the scientific ``active_space.json`` /
``natural_occupations.json`` records.  The typed
:class:`~cccp.calculation.results.CasscfPayload` is populated by the single
explicit mapping :func:`~cccp.calculation.results.casscf_payload_from_multireference`
from the legacy-shaped ``metadata["multireference"]`` dict (same keys the ACP
primitive produced) so the ACP wrapper can preserve legacy metadata.

Pre-launch input problems raise ``TaskInputError``; scientific/runtime
failures return a structured ``TaskResult`` with a closed
:class:`~cccp.calculation.results.ErrorKind`.  This module never writes
manifests or platform frames and never imports ``acp``.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cccp.calculation._common import (
    CalculationInputs,
    artifacts_from_qc,
    backend_for_request,
    build_state_scf_options,
    call_capability,
    classify_failure,
    electron_count,
    error_text,
    load_geometry,
    qc_metadata_json,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import (
    ArtifactRef,
    CASSCFSpec,
    JsonValue,
    Provenance,
    validate_casscf_spec,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import CasscfOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import (
    CasscfPayload,
    ErrorKind,
    TaskResult,
    casscf_payload_from_multireference,
)

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Legacy message shared with ``casscf_spec_from_dict`` (contains "active").
_ACTIVE_SPACE_REQUIRED = (
    "CASSCF requires an active-space definition (active_electrons/active_orbitals)"
)


def run_casscf(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one CASSCF (optionally NEVPT2) calculation on one structure.

    ``request.options`` must be :class:`~cccp.calculation.requests.CasscfOptions`
    (the active-space spec); ``None`` raises ``TaskInputError`` with the
    legacy "active-space" message.  A non-ORCA backend is a structured failed
    result — CASSCF is only supported on the ORCA backend.  ``context.backend``
    (runtime seam) supplies an already-resolved backend instance;
    ``context.capability_extras`` carries verbatim legacy capability kwargs.
    """
    validate_request(request)
    if request.task is not TaskKind.CASSCF:
        message = f"run_casscf requires task 'casscf', got {request.task.value!r}"
        raise TaskInputError(message)
    options = request.options
    if options is None:
        raise TaskInputError(_ACTIVE_SPACE_REQUIRED)
    if not isinstance(options, CasscfOptions):
        message = "casscf requires CasscfOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    selected_backend = (request.backend or "orca").strip().lower()
    if selected_backend != "orca":
        message = f"CASSCF is only supported on the ORCA backend (got {selected_backend!r})"
        return TaskResult(
            task=TaskKind.CASSCF,
            status="failed",
            complete=False,
            error_kind=ErrorKind.UNSUPPORTED_CAPABILITY,
            errors=(message,),
            provenance=_provenance(selected_backend, request),
        )

    structure = request.structure
    if structure is None:
        message = "task 'casscf' requires a structure input"
        raise TaskInputError(message)
    path = structure.path
    if path is not None and not path.is_absolute():
        path = ctx.input_root() / path
    coordinates, symbols = load_geometry(
        path=path,
        coordinates=structure.coordinates,
        symbols=structure.symbols,
        elements=structure.elements,
    )

    spec = options.spec
    validation_errors = validate_casscf_spec(
        spec, n_electrons=electron_count(symbols, request.charge)
    )
    if validation_errors:
        return TaskResult(
            task=TaskKind.CASSCF,
            status="failed",
            complete=False,
            error_kind=ErrorKind.INVALID_INPUT,
            errors=tuple(validation_errors),
            provenance=_provenance(selected_backend, request),
        )

    state = request.electronic_state
    multiplicity = spec.multiplicity
    state_scf = None
    if state is not None:
        multiplicity = state.target_multiplicity
        state_scf = build_state_scf_options(state)
    inputs = CalculationInputs(
        coordinates=coordinates,
        symbols=symbols,
        charge=request.charge,
        multiplicity=multiplicity,
        scf_options=state_scf,
        electronic_state=state,
    )

    kwargs: dict[str, Any] = {
        key: value for key, value in (ctx.capability_extras or {}).items() if key != "casscf"
    }
    kwargs.update(
        {
            "active_electrons": spec.active_electrons,
            "active_orbitals": spec.active_orbitals,
            "nroots": spec.nroots,
            "state_weights": list(spec.state_weights),
            "dynamic_correlation": spec.dynamic_correlation.value,
            "active_orbital_indices": list(spec.active_orbital_indices),
            "frozen_core": spec.frozen_core,
        }
    )
    if spec.orbital_source is not None:
        kwargs["orbital_source"] = str(spec.orbital_source)
    if spec.max_iterations is not None:
        kwargs["max_iterations"] = spec.max_iterations
    if inputs.scf_options:
        kwargs["scf_options"] = dict(inputs.scf_options)

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(
            selected_backend,
            config=ctx.config,
            constructor_kwargs={
                key: value for key, value in kwargs.items() if key != "output_name"
            },
        )
    )
    backend_label = str(getattr(backend, "name", selected_backend) or selected_backend)
    target_dir = request.output_dir

    try:
        qc_result = call_capability(backend, "casscf", inputs, target_dir, kwargs)
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.CASSCF,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=_provenance(backend_label, request),
        )

    artifacts = list(artifacts_from_qc(qc_result, backend_label))
    metadata: dict[str, JsonValue] = qc_metadata_json(qc_result.metadata)

    if not qc_result.success:
        message = qc_result.error_message or "CASSCF calculation failed"
        return TaskResult(
            task=TaskKind.CASSCF,
            status="failed",
            complete=False,
            error_kind=classify_failure(error_message=message),
            errors=(message,),
            energy_hartree=qc_result.energy,
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result),
            artifacts=tuple(artifacts),
            provenance=_provenance(backend_label, request),
            metadata=metadata,
        )
    if qc_result.energy is None:
        return TaskResult(
            task=TaskKind.CASSCF,
            status="failed",
            complete=False,
            error_kind=classify_failure(missing_energy=True),
            errors=("CASSCF calculation returned no energy",),
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result),
            artifacts=tuple(artifacts),
            provenance=_provenance(backend_label, request),
            metadata=metadata,
        )

    multireference = _multireference_metadata(qc_result, spec, multiplicity)
    artifacts.extend(_write_active_space_artifacts(target_dir, multireference, backend_label))
    metadata["multireference"] = multireference
    payload: CasscfPayload = casscf_payload_from_multireference(multireference)
    return TaskResult(
        task=TaskKind.CASSCF,
        status="completed",
        complete=True,
        errors=(),
        energy_hartree=qc_result.energy,
        coordinates=_coordinates(qc_result),
        symbols=_symbols(qc_result),
        artifacts=tuple(artifacts),
        provenance=_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


def _multireference_metadata(
    qc_result: object,
    spec: CASSCFSpec,
    multiplicity: int,
) -> dict[str, JsonValue]:
    """Build the legacy-shaped ``multireference`` dict (production keys).

    Same key set the ACP primitive's ``_multireference_metadata`` produced
    (``casscf_payload_from_multireference`` is the single consumer mapping).
    """
    raw = getattr(qc_result, "metadata", None)
    raw = raw.get("casscf") if isinstance(raw, dict) else {}
    parsed = raw if isinstance(raw, dict) else {}
    return {
        "active_electrons": spec.active_electrons,
        "active_orbitals": spec.active_orbitals,
        "multiplicity": multiplicity,
        "nroots": spec.nroots,
        "state_weights": list(spec.state_weights),
        "active_orbital_indices": list(spec.active_orbital_indices),
        "orbital_source": str(spec.orbital_source) if spec.orbital_source else None,
        "dynamic_correlation": spec.dynamic_correlation.value,
        "active_space_signature": spec.active_space_signature(),
        "casscf_energy_hartree": parsed.get("casscf_energy_hartree"),
        "nevpt2_correction_hartree": parsed.get("nevpt2_correction_hartree"),
        "correlated_energy_hartree": parsed.get("correlated_energy_hartree"),
        "natural_occupations": parsed.get("natural_occupations") or [],
        "nevpt2_roots": parsed.get("nevpt2_roots") or [],
        "converged": bool(parsed.get("converged")),
    }


def _write_active_space_artifacts(
    target_dir: Path | None,
    multireference: dict[str, JsonValue],
    backend: str,
) -> list[ArtifactRef]:
    """Persist the scientific active-space records next to the QC output."""
    if target_dir is None:
        return []
    artifacts: list[ArtifactRef] = []
    try:
        active_space_file = target_dir / "active_space.json"
        active_space_file.write_text(
            json.dumps(multireference, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        artifacts.append(ArtifactRef(path=active_space_file, type="active_space", source=backend))
        occupations = multireference.get("natural_occupations")
        if isinstance(occupations, list) and occupations:
            occupations_file = target_dir / "natural_occupations.json"
            occupations_file.write_text(
                json.dumps({"occupations": occupations}, indent=2),
                encoding="utf-8",
            )
            artifacts.append(
                ArtifactRef(path=occupations_file, type="natural_occupations", source=backend)
            )
    except OSError as exc:
        logger.warning("failed to write CASSCF artifacts in %s: %s", target_dir, exc)
    return artifacts


def _coordinates(qc_result: object) -> tuple[tuple[float, float, float], ...] | None:
    raw = getattr(qc_result, "coordinates", None)
    if raw is None:
        return None
    return tuple(tuple(float(c) for c in row) for row in raw)


def _symbols(qc_result: object) -> tuple[str, ...] | None:
    raw = getattr(qc_result, "symbols", None)
    if not raw:
        return None
    return tuple(str(s) for s in raw)


def _provenance(backend: str, request: TaskRequest) -> Provenance:
    return Provenance(
        backend=backend,
        method=request.level.method,
        version="unknown",
        input_signature=str(request.structure.path) if request.structure else "",
    )


__all__ = ["run_casscf"]
