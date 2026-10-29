"""xTB PATH search task core (plan todo 42 — GFN2-xTB metadynamics path).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (structure pair → start/end XYZ,
typed options + scoped fragments → capability kwargs) → interface execution
→ typed :class:`~cccp.calculation.results.XtbPathSearchPayload`.

The request consumes the CCCP typed envelope only: the legacy
``pes2ts_xtb_path_request_v1`` payload is mapped onto
:class:`~cccp.calculation.requests.XtbPathSearchOptions` (plus scoped
``path_inp_text`` / ``extra_args`` fragments) by the ACP adapter and is
never parsed here.  Frame indices follow path order; the endpoints
reference those indices.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cccp.calculation._common import (
    backend_for_request,
    classify_failure,
    error_text,
    load_geometry,
    qc_metadata_json,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import ArtifactRef, JsonValue
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    FRAGMENT_KNOB_TOKENS,
    BackendInputFragment,
    BackendInputKind,
    TaskKind,
    TaskRequest,
    XtbPathSearchOptions,
    validate_request,
)
from cccp.calculation.results import (
    ErrorKind,
    TaskResult,
    XtbPathFrame,
    XtbPathSearchPayload,
)
from cccp.calculation.selection import precheck_runtime, select_semantic
from cccp.calculation.tasks.conformer_search import (
    artifact_ref,
    energy_from_title,
    execution_provenance,
    resolve_operation,
    write_input_xyz,
)

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Semantic capability → backend execution method and its call shape.
_CAPABILITY_METHODS: tuple[tuple[str, str], ...] = (
    ("path_search", "pair"),
    ("xtb_path_search", "pair"),
)

#: Verbatim legacy extras accepted by the ``path_search`` capability surface.
_EXTRA_ALLOWLIST = frozenset(
    {
        "nrun",
        "npoint",
        "anopt",
        "kpush",
        "kpull",
        "ppull",
        "alp",
        "etemp",
        "solvent",
        "timeout",
    }
)


def run_xtb_path_search(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one GFN2-xTB PATH metadynamics search (trajectory/frames/endpoints).

    Three-state semantics (``cccp.calculation.requests.P2_TASK_CONTRACTS``):
    success = completed with ``trajectory_ref``, frames and endpoint frame
    indices; partial = ``complete=False`` keeping valid frames at original
    indices; empty = zero frames is a failed result with
    ``error_kind=backend_failure``.
    """
    validate_request(request)
    if request.task is not TaskKind.XTB_PATH_SEARCH:
        message = f"run_xtb_path_search requires task 'xtb_path_search', got {request.task.value!r}"
        raise TaskInputError(message)
    if not isinstance(request.options, XtbPathSearchOptions):
        message = "xtb_path_search requires XtbPathSearchOptions"
        raise TaskInputError(message)
    options = request.options
    ctx = resolve_context(request, context)

    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    start_coords, start_symbols = _pair_geometry(request.structure, ctx, "structure")
    end_coords, end_symbols = _pair_geometry(options.end_structure, ctx, "options.end_structure")

    kwargs = _capability_kwargs(request, options, ctx)

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(
            selection.backend, config=ctx.config, resources=request.resources
        )
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = Path(
        request.output_dir if request.output_dir is not None else (ctx.workdir or Path.cwd())
    )

    _, _, operation = resolve_operation(backend, _CAPABILITY_METHODS)
    try:
        start_xyz = write_input_xyz(target_dir, start_coords, start_symbols, "path_start.xyz")
        end_xyz = write_input_xyz(target_dir, end_coords, end_symbols, "path_end.xyz")
        raw_result = operation(
            start_xyz,
            end_xyz,
            target_dir,
            charge=request.charge,
            multiplicity=request.multiplicity,
            **kwargs,
        )
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.XTB_PATH_SEARCH,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=execution_provenance(backend_label, request),
        )

    frames, trajectory_path, run_errors, metadata = _path_outcome(raw_result)

    artifacts = []
    trajectory_ref: ArtifactRef | None = None
    if trajectory_path is not None:
        trajectory_ref = artifact_ref(trajectory_path, "trajectory", backend_label)
        artifacts.append(trajectory_ref)
    log_files = (
        getattr(raw_result, "stdout_file", None),
        getattr(raw_result, "stderr_file", None),
    )
    for log_raw in log_files:
        if isinstance(log_raw, (str, Path)):
            artifacts.append(artifact_ref(Path(log_raw), "log", backend_label))

    if not frames:
        message = run_errors[0] if run_errors else "xTB path search returned zero frames"
        return TaskResult(
            task=TaskKind.XTB_PATH_SEARCH,
            status="failed",
            complete=False,
            error_kind=ErrorKind.BACKEND_FAILURE,
            errors=(message,),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            metadata=metadata,
        )

    payload = XtbPathSearchPayload(
        trajectory_ref=trajectory_ref,
        frames=frames,
        start_frame_index=0,
        end_frame_index=len(frames) - 1,
    )
    metadata["n_frames"] = len(frames)

    success = bool(getattr(raw_result, "success", True))
    partial = bool(run_errors) or not success
    if partial:
        return TaskResult(
            task=TaskKind.XTB_PATH_SEARCH,
            status="failed",
            complete=False,
            error_kind=None,
            errors=tuple(run_errors) or ("xTB path search completed with partial output",),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            payload=payload,
            metadata=metadata,
        )
    return TaskResult(
        task=TaskKind.XTB_PATH_SEARCH,
        status="completed",
        complete=True,
        errors=(),
        artifacts=tuple(artifacts),
        provenance=execution_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


def _pair_geometry(structure: Any, ctx: TaskContext, label: str) -> tuple[Any, tuple[str, ...]]:
    """Resolve one structure-pair member into ``(coordinates, symbols)``."""
    if structure is None:
        raise TaskInputError(f"xtb_path_search requires {label} (structure pair)")
    path = structure.path
    if path is not None and not path.is_absolute():
        path = ctx.input_root() / path
    try:
        return load_geometry(
            path=path,
            coordinates=structure.coordinates,
            symbols=structure.symbols,
            elements=structure.elements,
        )
    except ValueError as exc:
        raise TaskInputError(f"{label}: {exc}") from exc


def _capability_kwargs(
    request: TaskRequest, options: XtbPathSearchOptions, ctx: TaskContext
) -> dict[str, Any]:
    """Translation step: typed knobs + scoped fragments + filtered extras.

    Fragment content passes through verbatim (``path_inp_text`` body and
    ``extra_args`` flags are frozen-recipe inputs).
    """
    kwargs: dict[str, Any] = {
        key: value
        for key, value in dict(ctx.capability_extras or {}).items()
        if key in _EXTRA_ALLOWLIST and value is not None
    }
    if options.gfn_level is not None:
        kwargs["gfn_level"] = options.gfn_level
    if options.uhf is not None:
        kwargs["uhf"] = options.uhf
    if options.seed is not None:
        kwargs["seed"] = options.seed
    timeout = (
        request.resources.timeout_s if request.resources.timeout_s is not None else ctx.timeout_s
    )
    if timeout is not None:
        kwargs.setdefault("timeout", int(timeout))
    path_texts: list[str] = []
    extra_args: list[str] = []
    structured: dict[str, object] = {
        "charge": request.charge,
        "multiplicity": request.multiplicity,
        "nproc": request.resources.nproc,
        "maxcore": request.resources.maxcore,
        "gfn_level": options.gfn_level,
        "uhf": options.uhf,
        "seed": options.seed,
    }
    for fragment in options.backend_inputs:
        if fragment.kind is BackendInputKind.PATH_INP_TEXT:
            path_texts.append(str(fragment.content))
        elif fragment.kind is BackendInputKind.EXTRA_ARGS:
            extra_args.extend(
                _drop_structured_knobs(_fragment_args(fragment), fragment.kind, structured)
            )
    if len(path_texts) > 1:
        message = "xtb_path_search accepts at most one path_inp_text fragment"
        raise TaskInputError(message)
    if path_texts:
        kwargs["path_inp_text"] = path_texts[0]
    if extra_args:
        kwargs["extra_args"] = tuple(extra_args)
    return kwargs


def _drop_structured_knobs(
    args: Sequence[str], kind: BackendInputKind, structured: Mapping[str, object]
) -> list[str]:
    """Structured-fields-win: drop args that re-specify set structured knobs."""
    tokens = FRAGMENT_KNOB_TOKENS.get(kind, {})
    kept: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        drop = False
        for token, field_name in tokens.items():
            if structured.get(field_name) is None:
                continue
            if arg == token:
                drop = True
                skip_next = True
                break
            if arg.startswith(f"{token}="):
                drop = True
                break
        if not drop:
            kept.append(arg)
    return kept


def _fragment_args(fragment: BackendInputFragment) -> Sequence[str]:
    content = fragment.content
    if isinstance(content, str):
        return (content,)
    return tuple(str(item) for item in content)


def _path_outcome(
    raw_result: object,
) -> tuple[tuple[XtbPathFrame, ...], Path | None, list[str], dict[str, JsonValue]]:
    """Normalise a ``PathSearchResult`` (or QCResult-like) into typed frames."""
    errors: list[str] = []
    frame_paths_raw = getattr(raw_result, "frame_paths", None)
    energies_raw = getattr(raw_result, "energies_hartree", None)
    trajectory_raw = getattr(raw_result, "trajectory_file", None)
    trajectory_path = Path(trajectory_raw) if isinstance(trajectory_raw, (str, Path)) else None

    frames: list[XtbPathFrame] = []
    if isinstance(frame_paths_raw, Sequence) and not isinstance(frame_paths_raw, (str, bytes)):
        energies: list[float | None] = []
        if isinstance(energies_raw, Sequence) and not isinstance(energies_raw, (str, bytes)):
            energies = [float(value) if value is not None else None for value in energies_raw]
        for index, frame_path in enumerate(frame_paths_raw):
            energy = energies[index] if index < len(energies) else None
            if energy is None and isinstance(frame_path, (str, Path)):
                energy = _frame_energy(Path(frame_path))
            frames.append(XtbPathFrame(index=index, energy_hartree=energy))
    elif getattr(raw_result, "coordinates", None) is not None:
        # QCResult-like fallback: stacked multi-frame coordinates.
        coordinates = raw_result.coordinates
        symbols_raw = getattr(raw_result, "symbols", None) or []
        n_atoms = len(symbols_raw)
        total = int(getattr(coordinates, "shape", (0, 0))[0]) if n_atoms else 0
        frames = [XtbPathFrame(index=i, energy_hartree=None) for i in range(total // n_atoms)]

    error_message = getattr(raw_result, "error_message", None)
    if error_message:
        errors.append(str(error_message))
    metadata = qc_metadata_json(
        dict(getattr(raw_result, "metadata", {}) or {})
        if isinstance(getattr(raw_result, "metadata", None), Mapping)
        else {}
    )
    return tuple(frames), trajectory_path, errors, metadata


def _frame_energy(frame_path: Path) -> float | None:
    try:
        lines = frame_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return energy_from_title(lines[1]) if len(lines) >= 2 else None


__all__ = ["run_xtb_path_search"]
