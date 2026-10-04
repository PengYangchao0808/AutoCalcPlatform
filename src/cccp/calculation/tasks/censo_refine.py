"""CENSO refinement task core (plan todo 43 — P2 execution B).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation → ``CensoBackend.refine_ensemble``
execution → typed :class:`~cccp.calculation.results.CensoRefinePayload`.

Contract (``P2_TASK_CONTRACTS[TaskKind.CENSO_REFINE]``):

* input shape: ensemble (multi-frame XYZ ``structure.path``);
* success: per-conformer energy / free-energy / weight rows + refined
  ensemble artifact (``refined_ensemble_ref``);
* partial (``status="failed"``, ``complete=False``): valid rows are kept
  keyed by their original ``frame_index`` (CENSO ordering maps back);
* empty: zero surviving records is ``failed(error_kind=backend_failure)``.

CENSO template text is generated ONLY by the translation layer
(:func:`cccp.qc.translation.render_censo_template_lines`); raw per-part
route keywords arrive as ``part_template_extras`` in
``context.capability_extras`` and pre-assembled template lines are
rejected.  ``level_overrides`` become rcfile ``part_overrides`` (preset
refinement seam).  This module never writes manifests or platform frames.

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
    _artifact_ref,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import JsonValue, Provenance
from cccp.calculation.errors import TaskInputError, UnsupportedCapabilityError
from cccp.calculation.requests import CensoRefineOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import CensoRefinePayload, CensoRefineRecord, ErrorKind, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic
from cccp.qc.interfaces.censo import CensoError, part_index
from cccp.qc.translation import render_censo_template_lines

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError, CensoError)

#: Semantic capability → ``CensoBackend`` execution method name.
_CAPABILITY_METHODS: dict[str, str] = {"censo_refine": "refine_ensemble"}

#: ``context.capability_extras`` key carrying raw per-part route keywords
#: (sequences of literal keywords — never pre-assembled template text).
_PART_TEMPLATE_EXTRAS_KEY = "part_template_extras"

#: Forbidden extras key: pre-assembled CENSO template lines.  Template text
#: is a translation-layer product and must never be assembled upstream.
_RAW_PART_TEMPLATES_KEY = "part_templates"


def run_censo_refine(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one CENSO ensemble refinement (the single-task core).

    ``context.backend`` (runtime seam) supplies an already-resolved backend
    instance and skips the runtime precheck.  ``context.capability_extras``
    carries verbatim legacy capability kwargs plus the raw per-part route
    keywords under ``part_template_extras`` (rendered into template lines
    here, by the translation layer).
    """
    validate_request(request)
    if request.task is not TaskKind.CENSO_REFINE:
        message = f"run_censo_refine requires task 'censo_refine', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, CensoRefineOptions):
        message = "censo_refine requires CensoRefineOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    # ① semantic selection; ② runtime precheck unless an instance is handed in.
    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:
        message = "task 'censo_refine' requires a structure input"
        raise TaskInputError(message)
    if structure.coordinates is not None:
        message = "censo_refine input shape is an ensemble file (structure.path)"
        raise TaskInputError(message)
    path = structure.path
    if path is None:
        message = "censo_refine input shape is an ensemble file (structure.path)"
        raise TaskInputError(message)
    if not path.is_absolute():
        path = ctx.input_root() / path

    options = request.options if isinstance(request.options, CensoRefineOptions) else None
    kwargs = _refine_kwargs(ctx, request, options)

    method_name = _CAPABILITY_METHODS.get(selection.capability)
    if method_name is None:
        message = f"no censo_refine execution method for capability {selection.capability!r}"
        raise UnsupportedCapabilityError(message)
    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(
            selection.backend,
            config=ctx.config,
            constructor_kwargs={
                key: value
                for key, value in kwargs.items()
                if key not in {"output_name", "part_templates", "part_template_extras"}
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
    target_dir = _target_dir(request, ctx)

    try:
        raw_result = operation(path, target_dir, **kwargs)
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.CENSO_REFINE,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=_provenance(backend_label, request),
        )

    return _map_result(raw_result, backend_label, request, target_dir)


# ── translation: structured options → refine_ensemble kwargs ───────────


def _refine_kwargs(
    ctx: TaskContext,
    request: TaskRequest,
    options: CensoRefineOptions | None,
) -> dict[str, Any]:
    """Build the ``refine_ensemble`` kwargs (translation-layer step).

    Verbatim legacy extras pass through; structured fields (preset /
    level overrides / temperature / charge / multiplicity / nproc) win;
    per-part template lines are rendered by
    :func:`cccp.qc.translation.render_censo_template_lines` from the raw
    ``part_template_extras`` keywords — never assembled here.
    """
    extras = dict(ctx.capability_extras or {})
    if _RAW_PART_TEMPLATES_KEY in extras:
        message = (
            "pre-assembled CENSO template lines are not accepted; pass raw "
            "per-part route keywords as capability_extras['part_template_extras'] "
            "(template text is generated by the translation layer)"
        )
        raise TaskInputError(message)
    template_extras = extras.pop(_PART_TEMPLATE_EXTRAS_KEY, None)

    kwargs: dict[str, Any] = {key: value for key, value in extras.items() if value is not None}

    if template_extras is not None:
        kwargs["part_templates"] = _render_part_templates(template_extras)

    if options is not None:
        if options.preset is not None:
            kwargs["preset"] = options.preset
        if options.temperature_k is not None:
            kwargs["temperature"] = options.temperature_k
        part_overrides = _part_overrides(options)
        if part_overrides:
            kwargs["part_overrides"] = part_overrides

    kwargs["charge"] = request.charge
    kwargs["multiplicity"] = request.multiplicity
    if request.resources.nproc is not None:
        kwargs["nproc"] = request.resources.nproc
    return kwargs


def _render_part_templates(template_extras: Mapping[str, Any]) -> dict[str, list[str]]:
    """Raw per-part route keywords → template lines via the translation layer."""
    if not isinstance(template_extras, Mapping):
        message = "capability_extras['part_template_extras'] must be a mapping of part → keywords"
        raise TaskInputError(message)
    templates: dict[str, list[str]] = {}
    for part, keywords in template_extras.items():
        if isinstance(keywords, str) or not isinstance(keywords, Sequence):
            message = (
                f"part_template_extras[{part!r}] must be a sequence of route "
                "keywords (never pre-assembled template text)"
            )
            raise TaskInputError(message)
        lines = render_censo_template_lines([str(item) for item in keywords])
        if lines:
            templates[str(part)] = lines
    return templates


def _part_overrides(options: CensoRefineOptions) -> dict[str, dict[str, Any]]:
    """``level_overrides`` → rcfile ``part_overrides`` (only set fields)."""
    part_overrides: dict[str, dict[str, Any]] = {}
    for override in options.level_overrides:
        part = str(override.part or "").strip()
        if not part:
            message = "options.level_overrides entries require a non-empty part name"
            raise TaskInputError(message)
        entry = part_overrides.setdefault(part, {})
        if override.func is not None:
            entry["func"] = override.func
        if override.basis is not None:
            entry["basis"] = override.basis
        if override.threshold is not None:
            entry["threshold"] = override.threshold
    return part_overrides


def _target_dir(request: TaskRequest, ctx: TaskContext) -> Path:
    target_dir = request.output_dir
    if target_dir is None:
        target_dir = ctx.workdir if ctx.workdir is not None else Path.cwd() / "censo_refine_work"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


# ── result mapping (record identity per P2_TASK_CONTRACTS) ─────────────


def _map_result(
    raw_result: Any,
    backend_label: str,
    request: TaskRequest,
    target_dir: Path,
) -> TaskResult:
    """Map a CENSO run result onto the typed payload + three-state semantics.

    Rows whose identity cannot map back to an original ensemble frame
    (``frame_index < 0``) are dropped; when any row is dropped — or the
    refined ensemble artifact is missing — the result is partial
    (``status="failed"``, ``complete=False``) with the valid rows kept at
    their original ``frame_index``.  Zero surviving rows is an empty
    result: ``failed(error_kind=backend_failure)``.
    """
    raw_records = list(getattr(raw_result, "records", None) or ())
    weights = _weights(raw_result)
    work_dir = getattr(raw_result, "work_dir", None)
    final_part = str(getattr(raw_result, "final_part", "") or "")

    records: list[CensoRefineRecord] = []
    dropped: list[str] = []
    for record in raw_records:
        conf_id = str(getattr(record, "conf_id", "") or "")
        frame_index = int(getattr(record, "frame_index", -1))
        if frame_index < 0:
            dropped.append(conf_id or "<unknown>")
            continue
        records.append(
            CensoRefineRecord(
                conf_id=conf_id,
                frame_index=frame_index,
                energy_hartree=_finite(getattr(record, "energy", None)),
                free_energy_hartree=_finite(getattr(record, "gtot", None)),
                weight=_finite(weights.get(conf_id)),
            )
        )

    provenance = _provenance(backend_label, request)
    metadata: dict[str, JsonValue] = {
        "preset": str(getattr(raw_result, "preset", "") or ""),
        "final_part": final_part,
        "temperature_k": float(getattr(raw_result, "temperature", 0.0) or 0.0),
        "record_count": len(records),
        "dropped_record_count": len(dropped),
    }

    if not records:
        message = (
            "censo refine produced no surviving records"
            + (f" (dropped: {', '.join(dropped)})" if dropped else "")
        )
        return TaskResult(
            task=TaskKind.CENSO_REFINE,
            status="failed",
            complete=False,
            error_kind=ErrorKind.BACKEND_FAILURE,
            errors=(message,),
            artifacts=(),
            provenance=provenance,
            metadata=metadata,
        )

    artifacts = []
    refined_ref = None
    errors: list[str] = []
    if final_part and work_dir is not None:
        ensemble_path = Path(work_dir) / f"{part_index(final_part)}_{final_part.upper()}.xyz"
        if ensemble_path.is_file():
            refined_ref = _artifact_ref(ensemble_path, "refined_ensemble", backend_label)
            artifacts.append(refined_ref)
        else:
            errors.append(f"refined ensemble artifact missing: {ensemble_path}")
    else:
        errors.append("refined ensemble artifact missing (no final part)")
    if dropped:
        errors.append(f"dropped records without original frame identity: {', '.join(dropped)}")

    payload = CensoRefinePayload(records=tuple(records), refined_ensemble_ref=refined_ref)
    complete = not errors
    return TaskResult(
        task=TaskKind.CENSO_REFINE,
        status="completed" if complete else "failed",
        complete=complete,
        error_kind=None if complete else classify_failure(error_message="; ".join(errors)),
        errors=tuple(errors),
        artifacts=tuple(artifacts),
        provenance=provenance,
        payload=payload,
        metadata=metadata,
    )


def _weights(raw_result: Any) -> dict[str, float]:
    getter = getattr(raw_result, "boltzmann_weights", None)
    if not callable(getter):
        return {}
    try:
        raw = getter()
    except (ArithmeticError, ValueError) as error:
        logger.warning("CENSO boltzmann weights unavailable: %s", error)
        return {}
    return {str(key): float(value) for key, value in dict(raw or {}).items()}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _provenance(backend: str, request: TaskRequest) -> Provenance:
    return Provenance(
        backend=backend,
        method=request.level.method,
        version="unknown",
        input_signature=str(request.structure.path) if request.structure else "",
    )


__all__ = ["run_censo_refine"]
