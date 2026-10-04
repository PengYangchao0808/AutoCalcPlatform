"""MD-sampling task core (plan todo 42 — Molclus/xTB-MD execution).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (geometry → input XYZ, typed options
→ capability kwargs) → interface execution → typed
:class:`~cccp.calculation.results.MdSamplingPayload`.

Scientific scope only: one MD trajectory artifact plus its valid frame
count.  Frame indices follow trajectory order and are never renumbered.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from cccp.calculation._common import (
    backend_for_request,
    classify_failure,
    error_text,
    level_explicit_fields,
    load_geometry,
    qc_metadata_json,
    render_backend_input,
    resolve_spec,
    theory_run_config,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import JsonValue
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import MdSamplingOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import ErrorKind, MdSamplingPayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic
from cccp.calculation.tasks.conformer_search import (
    artifact_ref,
    execution_provenance,
    normalize_capability_result,
    parse_ensemble_frames,
    resolve_operation,
    write_input_xyz,
)

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Semantic capability → backend execution method and its call shape.
_CAPABILITY_METHODS: tuple[tuple[str, str], ...] = (
    ("run_md", "path"),
    ("md_sampling", "path"),
    ("md", "path"),
)

#: Typed option field → ``run_md`` capability kwarg.
_OPTION_KWARGS: tuple[tuple[str, str], ...] = (
    ("md_method", "md_method"),
    ("gfn_level", "gfn_level"),
    ("temperature_k", "temperature"),
    ("time_ps", "time_ps"),
    ("dump_fs", "dump_fs"),
    ("step_fs", "step_fs"),
    ("hmass", "hmass"),
    ("shake", "shake"),
    ("nvt", "nvt"),
    ("seed", "seed"),
)

#: Verbatim legacy extras accepted by the ``run_md`` capability surface.
_EXTRA_ALLOWLIST = frozenset({"solvent", "solvent_model"})


def run_md_sampling(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one xTB-MD sampling trajectory (trajectory artifact + frame count).

    Three-state semantics (``cccp.calculation.requests.P2_TASK_CONTRACTS``):
    success = completed with ``trajectory_ref`` and ``n_frames >= 1``;
    partial = ``complete=False`` keeping the valid frame prefix at original
    indices; empty = zero frames is a failed result with
    ``error_kind=backend_failure``.
    """
    validate_request(request)
    if request.task is not TaskKind.MD_SAMPLING:
        message = f"run_md_sampling requires task 'md_sampling', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, MdSamplingOptions):
        message = "md_sampling requires MdSamplingOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:
        message = "task 'md_sampling' requires a structure input"
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

    options = request.options if isinstance(request.options, MdSamplingOptions) else None
    kwargs = _capability_kwargs(request, options, ctx)

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(selection.backend, config=ctx.config)
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = Path(
        request.output_dir if request.output_dir is not None else (ctx.workdir or Path.cwd())
    )

    _, _, operation = resolve_operation(backend, _CAPABILITY_METHODS)
    try:
        input_xyz = write_input_xyz(target_dir, coordinates, symbols, "md_input.xyz")
        raw_result = operation(
            input_xyz,
            charge=request.charge,
            multiplicity=request.multiplicity,
            output_dir=target_dir,
            **kwargs,
        )
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.MD_SAMPLING,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=execution_provenance(backend_label, request),
        )

    outcome = normalize_capability_result(raw_result)
    metadata: dict[str, JsonValue] = qc_metadata_json(outcome.metadata)
    trajectory_path = outcome.output_file

    frames, parse_errors = [], []
    if trajectory_path is not None and trajectory_path.is_file():
        frames, parse_errors = parse_ensemble_frames(trajectory_path)
    prefix = _valid_prefix_length(frames)

    errors: list[str] = []
    if outcome.error_message:
        errors.append(outcome.error_message)
    errors.extend(parse_errors)

    artifacts = []
    trajectory_ref = None
    if trajectory_path is not None:
        trajectory_ref = artifact_ref(trajectory_path, "trajectory", backend_label)
        artifacts.append(trajectory_ref)

    if prefix == 0:
        message = errors[0] if errors else "MD sampling returned an empty trajectory"
        return TaskResult(
            task=TaskKind.MD_SAMPLING,
            status="failed",
            complete=False,
            error_kind=ErrorKind.BACKEND_FAILURE,
            errors=(message,),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            metadata=metadata,
        )

    payload = MdSamplingPayload(trajectory_ref=trajectory_ref, n_frames=prefix)
    metadata["n_frames"] = prefix

    partial = bool(errors) or not outcome.success or prefix != len(frames)
    if partial:
        return TaskResult(
            task=TaskKind.MD_SAMPLING,
            status="failed",
            complete=False,
            error_kind=None,
            errors=tuple(errors) or ("MD sampling completed with partial output",),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            payload=payload,
            metadata=metadata,
        )
    return TaskResult(
        task=TaskKind.MD_SAMPLING,
        status="completed",
        complete=True,
        errors=(),
        artifacts=tuple(artifacts),
        provenance=execution_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


def _capability_kwargs(
    request: TaskRequest,
    options: MdSamplingOptions | None,
    ctx: TaskContext,
) -> dict[str, Any]:
    """Translation step: typed options + rendered level fields + scoped extras."""
    spec = resolve_spec(
        request.level.method or None,
        explicit=level_explicit_fields(request.level),
        run_config=theory_run_config(ctx.config),
    )
    rendered = render_backend_input(
        spec,
        method=None,
        extras=ctx.capability_extras,
    )
    kwargs: dict[str, Any] = {
        key: value
        for key, value in rendered.items()
        if key in _EXTRA_ALLOWLIST and value is not None
    }
    if options is not None:
        for option_field, kwarg in _OPTION_KWARGS:
            value = getattr(options, option_field)
            if value is not None:
                kwargs[kwarg] = value
    return kwargs


def _valid_prefix_length(frames: list[Any]) -> int:
    """Length of the leading contiguous frame run starting at index 0."""
    length = 0
    for frame in frames:
        if frame.index != length:
            break
        length += 1
    return length


__all__ = ["run_md_sampling"]
