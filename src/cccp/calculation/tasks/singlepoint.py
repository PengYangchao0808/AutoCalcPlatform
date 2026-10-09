"""Single-point energy task core (plan todo 17 — first minimal complete path).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (``resolve_spec`` +
``render_backend_input``) → interface execution → typed
:class:`~cccp.calculation.results.SinglePointPayload`.

Pre-launch rejections raise typed exceptions
(``TaskInputError``/``UnsupportedCapabilityError``/``BackendUnavailableError``);
scientific/runtime failures return a structured ``TaskResult`` with a closed
:class:`~cccp.calculation.results.ErrorKind`.  This module never writes
manifests or platform frames — ACP-side product registration lives in the
compat wrapper.

Author: QCcalc Team
"""

from __future__ import annotations

import logging

from cccp.calculation._common import (
    CalculationInputs,
    apply_stability_options,
    artifacts_from_qc,
    backend_for_request,
    build_state_scf_options,
    call_capability,
    classify_failure,
    electron_count,
    error_text,
    level_explicit_fields,
    load_geometry,
    qc_metadata_json,
    render_backend_input,
    resolve_multiplicity,
    resolve_spec,
    state_result_metadata,
    theory_run_config,
    write_state_artifacts,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import JsonValue, Provenance, validate_electronic_state_spec
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import SinglePointOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import SinglePointPayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)


def run_singlepoint(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one single-point energy calculation (the single-task core).

    ``context.backend`` (runtime seam) supplies an already-resolved backend
    instance and then skips the runtime precheck — the caller vouches for the
    instance.  ``context.capability_extras`` carries verbatim legacy
    capability kwargs until the translation-layer cleanup (plan todo 25).
    """
    validate_request(request)
    if request.task is not TaskKind.SINGLEPOINT:
        message = f"run_singlepoint requires task 'singlepoint', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, SinglePointOptions):
        message = "singlepoint requires SinglePointOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    # ① semantic selection; ② runtime precheck unless an instance is handed in.
    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:
        message = "task 'singlepoint' requires a structure input"
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

    state = request.electronic_state
    if state is not None:
        validation = validate_electronic_state_spec(
            state,
            backend=selection.backend,
            n_electrons=electron_count(symbols, request.charge),
            n_atoms=len(symbols),
        )
        if validation.errors:
            message = "electronic_state validation failed: " + "; ".join(validation.errors)
            raise TaskInputError(message)
        for warning in validation.warnings:
            logger.warning("electronic_state: %s", warning)
    multiplicity = resolve_multiplicity(state, symbols, request.charge, request.multiplicity)
    options = request.options if isinstance(request.options, SinglePointOptions) else None
    stability_check = options.stability_check if options is not None else None

    state_scf = build_state_scf_options(state) if state is not None else None
    scf_options: dict[str, object] | None = None
    if state_scf is not None or stability_check:
        scf_options = apply_stability_options(
            state_scf, state=state, stability_check=stability_check
        ) or None
    inputs = CalculationInputs(
        coordinates=coordinates,
        symbols=symbols,
        charge=request.charge,
        multiplicity=multiplicity,
        scf_options=scf_options,
        electronic_state=state,
    )

    # translation: resolve_spec (①) + render_backend_input (②)
    spec = resolve_spec(
        request.level.method or None,
        explicit=level_explicit_fields(request.level),
        run_config=theory_run_config(ctx.config),
    )
    kwargs = render_backend_input(
        spec,
        method=request.level.method or None,
        state_scf_options=state_scf,
        extras=ctx.capability_extras,
    )
    if stability_check:
        kwargs["scf_options"] = apply_stability_options(
            kwargs.get("scf_options"),
            state=state,
            stability_check=stability_check,
        )

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(
            selection.backend,
            config=ctx.config,
            constructor_kwargs={
                key: value for key, value in kwargs.items() if key != "output_name"
            },
        )
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = request.output_dir

    try:
        qc_result = call_capability(
            backend, selection.capability, inputs, target_dir, kwargs
        )
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.SINGLEPOINT,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=_provenance(backend_label, request),
        )

    diagnostics = _state_diagnostics(qc_result)
    state_metadata, state_errors, forced_status = (
        state_result_metadata(state, diagnostics) if state is not None else ({}, [], None)
    )
    artifacts = list(artifacts_from_qc(qc_result, backend_label))
    artifacts.extend(
        write_state_artifacts(state, diagnostics, target_dir, backend_label)
    )
    metadata: dict[str, JsonValue] = qc_metadata_json(qc_result.metadata)

    if not qc_result.success:
        message = qc_result.error_message or "single-point calculation failed"
        return TaskResult(
            task=TaskKind.SINGLEPOINT,
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
            task=TaskKind.SINGLEPOINT,
            status="failed",
            complete=False,
            error_kind=classify_failure(missing_energy=True),
            errors=("single-point calculation returned no energy",),
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result),
            artifacts=tuple(artifacts),
            provenance=_provenance(backend_label, request),
            metadata=metadata,
        )

    payload = SinglePointPayload(electronic_state=state_metadata or None)
    if state_errors:
        return TaskResult(
            task=TaskKind.SINGLEPOINT,
            status=forced_status or "failed",
            complete=False,
            errors=tuple(state_errors),
            energy_hartree=qc_result.energy,
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result),
            artifacts=tuple(artifacts),
            provenance=_provenance(backend_label, request),
            payload=payload,
            metadata=metadata,
        )
    return TaskResult(
        task=TaskKind.SINGLEPOINT,
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


def _state_diagnostics(qc_result: object) -> dict[str, object]:
    metadata = getattr(qc_result, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("electronic_state_diagnostics")
    return dict(raw) if isinstance(raw, dict) else {}


def _provenance(backend: str, request: TaskRequest) -> Provenance:
    return Provenance(
        backend=backend,
        method=request.level.method,
        version="unknown",
        input_signature=str(request.structure.path) if request.structure else "",
    )


__all__ = ["run_singlepoint"]
