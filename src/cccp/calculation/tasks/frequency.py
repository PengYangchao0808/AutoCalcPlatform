"""Frequency task core (plan todo 19 — frequency science in cccp).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (``resolve_spec`` +
``render_backend_input``) → interface execution → typed
:class:`~cccp.calculation.results.FrequencyPayload` with the parsed
scientific data (:class:`~cccp.calculation.results.FrequencyAnalysis` —
frequencies / vibration vectors / IR intensities, one parse via
:mod:`cccp.calculation.frequency_parse`).

The task returns scientific data and scientific artifacts only: the
``normal_modes.json`` product format, geometry binding and manifest
registration are published by the ACP compat wrapper.  Pre-launch
rejections raise typed exceptions; scientific/runtime failures return a
structured ``TaskResult`` with a closed :class:`~cccp.calculation.results.ErrorKind`.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from pathlib import Path

from cccp.calculation._common import (
    CalculationInputs,
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
from cccp.calculation.contracts import (
    ArtifactRef,
    JsonValue,
    Provenance,
    validate_electronic_state_spec,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.frequency_parse import parse_frequency_log
from cccp.calculation.requests import FrequencyOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import FrequencyAnalysis, FrequencyPayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)


def run_frequency(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one vibrational frequency calculation (the single-task core).

    ``context.backend`` (runtime seam) supplies an already-resolved backend
    instance and then skips the runtime precheck.  ``context.capability_extras``
    carries verbatim legacy capability kwargs until the translation-layer
    cleanup (plan todo 25).  The returned payload's ``analysis`` is the full
    scientific parse (vibration vectors / IR intensities) — no ACP
    interpretation is required to consume it.
    """
    validate_request(request)
    if request.task is not TaskKind.FREQUENCY:
        message = f"run_frequency requires task 'frequency', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, FrequencyOptions):
        message = "frequency requires FrequencyOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    # ① semantic selection; ② runtime precheck unless an instance is handed in.
    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:
        message = "task 'frequency' requires a structure input"
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
    state_scf = build_state_scf_options(state) if state is not None else None
    inputs = CalculationInputs(
        coordinates=coordinates,
        symbols=symbols,
        charge=request.charge,
        multiplicity=multiplicity,
        scf_options=state_scf,
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

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(
            selection.backend,
            config=ctx.config,
            resources=request.resources,
            constructor_kwargs={
                key: value for key, value in kwargs.items() if key != "output_name"
            },
        )
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = request.output_dir

    try:
        qc_result = call_capability(backend, selection.capability, inputs, target_dir, kwargs)
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.FREQUENCY,
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
    artifacts.extend(write_state_artifacts(state, diagnostics, target_dir, backend_label))
    metadata: dict[str, JsonValue] = qc_metadata_json(qc_result.metadata)
    frequencies = tuple(float(value) for value in (qc_result.frequencies or ()))

    if not qc_result.success:
        message = qc_result.error_message or "frequency calculation failed"
        return TaskResult(
            task=TaskKind.FREQUENCY,
            status="failed",
            complete=False,
            error_kind=classify_failure(error_message=message),
            errors=(message,),
            energy_hartree=qc_result.energy,
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result) or tuple(symbols),
            frequencies=frequencies,
            artifacts=tuple(artifacts),
            provenance=_provenance(backend_label, request),
            metadata=metadata,
        )

    analysis = _analysis_from_logs(qc_result)
    payload = FrequencyPayload(
        n_imaginary=sum(1 for freq in frequencies if freq < 0.0),
        freq_log_ref=_freq_log_ref(artifacts),
        analysis=analysis,
        electronic_state=state_metadata or None,
    )
    if state_errors:
        return TaskResult(
            task=TaskKind.FREQUENCY,
            status=forced_status or "failed",
            complete=False,
            errors=tuple(state_errors),
            energy_hartree=qc_result.energy,
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result) or tuple(symbols),
            frequencies=frequencies,
            artifacts=tuple(artifacts),
            provenance=_provenance(backend_label, request),
            payload=payload,
            metadata=metadata,
        )
    return TaskResult(
        task=TaskKind.FREQUENCY,
        status="completed",
        complete=True,
        errors=(),
        energy_hartree=qc_result.energy,
        coordinates=_coordinates(qc_result),
        symbols=_symbols(qc_result) or tuple(symbols),
        frequencies=frequencies,
        artifacts=tuple(artifacts),
        provenance=_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


def _analysis_from_logs(qc_result: object) -> FrequencyAnalysis | None:
    """Parse the frequency log (``freq_log_file`` preferred over ``log_file``).

    Never fails the step — missing/unparsable logs yield ``None`` (the
    frequency list on the result stays authoritative).
    """
    log_path = _frequency_log_path(qc_result)
    if log_path is None:
        logger.debug("frequency: no log file found for the scientific parse; skipping")
        return None
    return parse_frequency_log(log_path)


def _frequency_log_path(qc_result: object) -> Path | None:
    for field_name in ("freq_log_file", "log_file"):
        raw_path = getattr(qc_result, field_name, None)
        if not isinstance(raw_path, (str, Path)) or not str(raw_path):
            continue
        path = Path(raw_path)
        if path.is_file():
            return path
    return None


def _freq_log_ref(artifacts: list[ArtifactRef]) -> ArtifactRef | None:
    """Reference to the frequency log artifact (``frequency_log`` preferred)."""
    for artifact in artifacts:
        if artifact.type == "frequency_log":
            return artifact
    for artifact in artifacts:
        if artifact.type == "log":
            return artifact
    return None


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


__all__ = ["run_frequency"]
