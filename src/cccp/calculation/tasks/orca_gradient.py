"""ORCA single-point gradient task core (plan todo 43 — P2 execution B).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (``resolve_spec`` +
``render_backend_input`` + scoped ``BackendInputFragment`` projection) →
``ORCABackend.single_point_gradient`` (ORCA ``EnGrad``) → typed
:class:`~cccp.calculation.results.OrcaGradientPayload`.

Contract (``P2_TASK_CONTRACTS[TaskKind.ORCA_GRADIENT]``):

* success: one gradient row per input atom (unit + convention recorded);
* partial: n/a — a gradient is all-or-nothing per atom order (a row-count
  mismatch is a failure, never a partial subset);
* empty: missing gradient rows is ``failed(error_kind=parse_failure)``.

The task consumes the CCCP typed request; the legacy frozen
``pes2ts_orca_gradient_request_v1`` payload is mapped onto it by the ACP
adapter (todo 24) before the call.  Gradients are the ORCA-printed energy
gradient dE/dX in Hartree/bohr — never forces, never fabricated.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from cccp.calculation._common import (
    CalculationInputs,
    _artifact_ref,
    artifacts_from_qc,
    backend_for_request,
    classify_failure,
    error_text,
    level_explicit_fields,
    load_geometry,
    qc_metadata_json,
    render_backend_input,
    resolve_multiplicity,
    resolve_spec,
    theory_run_config,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import JsonValue, Provenance
from cccp.calculation.errors import TaskInputError, UnsupportedCapabilityError
from cccp.calculation.requests import (
    BackendInputFragment,
    BackendInputKind,
    OrcaGradientOptions,
    TaskKind,
    TaskRequest,
    validate_request,
)
from cccp.calculation.results import ErrorKind, OrcaGradientPayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Semantic capability → ``ORCABackend`` execution method name.
_CAPABILITY_METHODS: dict[str, str] = {"orca_gradient": "single_point_gradient"}


def run_orca_gradient(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one ORCA ``EnGrad`` single-point gradient (the single-task core).

    ``context.backend`` (runtime seam) supplies an already-resolved backend
    instance and then skips the runtime precheck.  ``context.capability_extras``
    carries verbatim legacy capability kwargs; the scoped raw ORCA input
    (``route_extras`` / ``extra_blocks`` / ``output_name``) travels as
    ``OrcaGradientOptions.backend_inputs`` fragments.
    """
    validate_request(request)
    if request.task is not TaskKind.ORCA_GRADIENT:
        message = f"run_orca_gradient requires task 'orca_gradient', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, OrcaGradientOptions):
        message = "orca_gradient requires OrcaGradientOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    # ① semantic selection; ② runtime precheck unless an instance is handed in.
    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:
        message = "task 'orca_gradient' requires a structure input"
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
    multiplicity = resolve_multiplicity(None, symbols, request.charge, request.multiplicity)
    inputs = CalculationInputs(
        coordinates=coordinates,
        symbols=symbols,
        charge=request.charge,
        multiplicity=multiplicity,
    )

    options = request.options if isinstance(request.options, OrcaGradientOptions) else None

    # translation: resolve_spec (①) + render_backend_input (②) + fragments
    spec = resolve_spec(
        request.level.method or None,
        explicit=level_explicit_fields(request.level),
        run_config=theory_run_config(ctx.config),
    )
    kwargs = render_backend_input(
        spec,
        method=request.level.method or None,
        extras=ctx.capability_extras,
    )
    if options is not None:
        _apply_fragments(kwargs, options.backend_inputs)

    method_name = _CAPABILITY_METHODS.get(selection.capability)
    if method_name is None:
        message = f"no orca_gradient execution method for capability {selection.capability!r}"
        raise UnsupportedCapabilityError(message)
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
    operation = getattr(backend, method_name, None)
    if not callable(operation):
        message = (
            f"backend {type(backend).__name__} does not implement capability "
            f"{selection.capability!r}"
        )
        raise UnsupportedCapabilityError(message)
    target_dir = request.output_dir

    try:
        result = operation(
            inputs.coordinates,
            list(inputs.symbols),
            charge=inputs.charge,
            multiplicity=inputs.multiplicity,
            output_dir=target_dir,
            **kwargs,
        )
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.ORCA_GRADIENT,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=_provenance(backend_label, request),
        )

    return _map_result(result, inputs, backend_label, request, target_dir)


# ── translation: scoped fragments → capability kwargs ──────────────────


def _apply_fragments(kwargs: dict[str, Any], fragments: tuple[BackendInputFragment, ...]) -> None:
    """Project scoped raw fragments onto the capability kwargs (verbatim).

    ``route_extras`` / ``extra_blocks`` fragments accumulate in order;
    the ``output_name`` fragment is the last one written.  Fragments are
    the request-level truth and override extras-derived values.
    """
    for fragment in fragments:
        content = fragment.content
        items = [content] if isinstance(content, str) else [str(item) for item in content]
        if fragment.kind is BackendInputKind.ROUTE_EXTRAS:
            existing = kwargs.get("route_extras")
            merged = [str(item) for item in existing] if isinstance(existing, (list, tuple)) else []
            merged.extend(items)
            kwargs["route_extras"] = merged
        elif fragment.kind is BackendInputKind.EXTRA_BLOCKS:
            existing = kwargs.get("extra_blocks")
            merged = [str(item) for item in existing] if isinstance(existing, (list, tuple)) else []
            merged.extend(items)
            kwargs["extra_blocks"] = merged
        elif fragment.kind is BackendInputKind.OUTPUT_NAME:
            kwargs["output_name"] = items[-1] if items else ""


# ── result mapping (record identity per P2_TASK_CONTRACTS) ─────────────


def _map_result(
    result: Any,
    inputs: CalculationInputs,
    backend_label: str,
    request: TaskRequest,
    target_dir: Any,
) -> TaskResult:
    """Map a single-point gradient result onto the typed payload.

    Record identity: gradient row ``i`` is the gradient of input atom ``i``
    in ``gradient_unit`` / ``gradient_convention``.  A missing gradient or
    a row-count mismatch is ``failed(error_kind=parse_failure)`` — the
    gradient is all-or-nothing per atom order (never a partial subset).
    """
    energy = getattr(result, "energy", None)
    artifacts = list(artifacts_from_qc(result, backend_label))
    artifacts.extend(_engrad_artifacts(result, target_dir, backend_label))
    metadata: dict[str, JsonValue] = qc_metadata_json(getattr(result, "metadata", {}) or {})
    provenance = _provenance(backend_label, request)
    coordinates = _coordinates(result)
    symbols = _symbols(result) or inputs.symbols

    if not getattr(result, "success", False):
        message = getattr(result, "error_message", None) or "ORCA gradient calculation failed"
        return TaskResult(
            task=TaskKind.ORCA_GRADIENT,
            status="failed",
            complete=False,
            error_kind=classify_failure(error_message=message),
            errors=(message,),
            energy_hartree=energy,
            coordinates=coordinates,
            symbols=symbols,
            artifacts=tuple(artifacts),
            provenance=provenance,
            metadata=metadata,
        )

    rows = _gradient_rows(result)
    if rows is None or len(rows) != len(inputs.symbols):
        found = 0 if rows is None else len(rows)
        message = (
            f"ORCA gradient rows missing or misaligned: got {found} rows "
            f"for {len(inputs.symbols)} input atoms"
        )
        return TaskResult(
            task=TaskKind.ORCA_GRADIENT,
            status="failed",
            complete=False,
            error_kind=ErrorKind.PARSE_FAILURE,
            errors=(message,),
            energy_hartree=energy,
            coordinates=coordinates,
            symbols=symbols,
            artifacts=tuple(artifacts),
            provenance=provenance,
            metadata=metadata,
        )

    defaults = OrcaGradientPayload()
    payload = OrcaGradientPayload(
        gradients=tuple(rows),
        energy_hartree=energy,
        gradient_unit=str(getattr(result, "gradient_unit", None) or defaults.gradient_unit),
        gradient_convention=str(
            getattr(result, "gradient_convention", None) or defaults.gradient_convention
        ),
    )
    return TaskResult(
        task=TaskKind.ORCA_GRADIENT,
        status="completed",
        complete=True,
        errors=(),
        energy_hartree=energy,
        coordinates=coordinates,
        symbols=symbols,
        artifacts=tuple(artifacts),
        provenance=provenance,
        payload=payload,
        metadata=metadata,
    )


def _gradient_rows(result: Any) -> list[tuple[float, float, float]] | None:
    gradient = getattr(result, "gradient", None)
    if gradient is None:
        return None
    try:
        rows = [tuple(float(component) for component in row) for row in gradient]
    except (TypeError, ValueError):
        return None
    for row in rows:
        if len(row) != 3:
            return None
    return [(row[0], row[1], row[2]) for row in rows]


def _engrad_artifacts(result: Any, target_dir: Any, backend_label: str) -> list[Any]:
    """Reference the parsed ``.engrad`` companion file when one was bound."""
    source = str(getattr(result, "gradient_source", "") or "")
    if not source.startswith("engrad_file:"):
        return []
    name = source.split(":", 1)[1]
    if not name:
        return []
    candidates: list[Path] = []
    for anchor in (getattr(result, "output_file", None), getattr(result, "log_file", None)):
        if anchor is not None:
            candidates.append(Path(anchor).parent / name)
    if target_dir is not None:
        candidates.append(Path(target_dir) / name)
    for path in candidates:
        if path.is_file():
            return [_artifact_ref(path, "engrad", backend_label)]
    return []


def _coordinates(result: object) -> tuple[tuple[float, float, float], ...] | None:
    raw = getattr(result, "coordinates", None)
    if raw is None:
        return None
    return tuple(tuple(float(c) for c in row) for row in raw)


def _symbols(result: object) -> tuple[str, ...] | None:
    raw = getattr(result, "symbols", None)
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


__all__ = ["run_orca_gradient"]
