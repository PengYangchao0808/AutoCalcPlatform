"""NMR shielding task core (plan todo 43 — P2 execution B).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (``resolve_spec`` +
``render_backend_input``) → ``ORCABackend.nmr_shielding`` (GIAO) → typed
:class:`~cccp.calculation.results.NmrShieldingPayload`.

Contract (``P2_TASK_CONTRACTS[TaskKind.NMR_SHIELDING]``):

* success: shieldings keyed ``atom → {symbol, isotropic}``;
* partial (``status="failed"``, ``complete=False``): the shielded-atom
  subset is kept at the original keys;
* empty: zero shieldings parsed is ``failed(error_kind=parse_failure)``.

Record identity: atom keys keep their integer identity under the declared
``atom_index_base`` (the parser's 0-based indices shifted by the base), so
the JSON round-trip ``str keys → int keys`` restores exactly the same
table.  DP4/DP5 science is untouched here — this task stops at the
shielding table.

Author: QCcalc Team
"""

from __future__ import annotations

import logging

from cccp.calculation._common import (
    CalculationInputs,
    artifacts_from_qc,
    backend_for_request,
    call_capability,
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
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import NmrShieldingOptions, TaskKind, TaskRequest, validate_request
from cccp.calculation.results import ErrorKind, NmrShielding, NmrShieldingPayload, TaskResult
from cccp.calculation.selection import precheck_runtime, select_semantic

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)


def run_nmr_shielding(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one GIAO NMR shielding calculation (the single-task core).

    ``context.backend`` (runtime seam) supplies an already-resolved backend
    instance and then skips the runtime precheck.  ``context.capability_extras``
    carries verbatim legacy capability kwargs (``nuclei``, ``output_name``, …).
    """
    validate_request(request)
    if request.task is not TaskKind.NMR_SHIELDING:
        message = f"run_nmr_shielding requires task 'nmr_shielding', got {request.task.value!r}"
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, NmrShieldingOptions):
        message = "nmr_shielding requires NmrShieldingOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    # ① semantic selection; ② runtime precheck unless an instance is handed in.
    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:
        message = "task 'nmr_shielding' requires a structure input"
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
    multiplicity = resolve_multiplicity(
        None, symbols, request.charge, request.multiplicity
    )
    inputs = CalculationInputs(
        coordinates=coordinates,
        symbols=symbols,
        charge=request.charge,
        multiplicity=multiplicity,
    )

    options = request.options if isinstance(request.options, NmrShieldingOptions) else None

    # translation: resolve_spec (①) + render_backend_input (②)
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
            task=TaskKind.NMR_SHIELDING,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=_provenance(backend_label, request),
        )

    artifacts = list(artifacts_from_qc(qc_result, backend_label))
    metadata: dict[str, JsonValue] = qc_metadata_json(
        {key: value for key, value in (qc_result.metadata or {}).items() if key != "shieldings"}
    )
    provenance = _provenance(backend_label, request)

    if not qc_result.success:
        message = qc_result.error_message or "NMR shielding calculation failed"
        return TaskResult(
            task=TaskKind.NMR_SHIELDING,
            status="failed",
            complete=False,
            error_kind=classify_failure(error_message=message),
            errors=(message,),
            energy_hartree=qc_result.energy,
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result),
            artifacts=tuple(artifacts),
            provenance=provenance,
            metadata=metadata,
        )

    raw_meta = qc_result.metadata if isinstance(qc_result.metadata, dict) else {}
    parsed = raw_meta.get("shieldings", {})
    if not isinstance(parsed, dict):
        parsed = {}
    base = options.atom_index_base if options is not None else 0
    requested = tuple(options.atom_indices) if options is not None else ()

    shieldings, missing, invalid = _map_shieldings(parsed, base, requested)
    metadata["atom_index_base"] = base
    metadata["shielding_count"] = len(shieldings)
    payload = NmrShieldingPayload(shieldings=shieldings)

    if not shieldings:
        message = (
            "zero shieldings parsed"
            if not parsed
            else f"no requested atom shieldings found (missing: {', '.join(missing)})"
        )
        if invalid:
            message = f"{message}; unparsable entries: {', '.join(invalid)}"
        return TaskResult(
            task=TaskKind.NMR_SHIELDING,
            status="failed",
            complete=False,
            error_kind=ErrorKind.PARSE_FAILURE,
            errors=(message,),
            energy_hartree=qc_result.energy,
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result),
            artifacts=tuple(artifacts),
            provenance=provenance,
            payload=payload,
            metadata=metadata,
        )

    errors: list[str] = []
    if missing:
        errors.append(f"missing shieldings for requested atoms: {', '.join(missing)}")
    if invalid:
        errors.append(f"unparsable shielding entries: {', '.join(invalid)}")
    complete = not errors
    return TaskResult(
        task=TaskKind.NMR_SHIELDING,
        status="completed" if complete else "failed",
        complete=complete,
        error_kind=None if complete else ErrorKind.PARSE_FAILURE,
        errors=tuple(errors),
        energy_hartree=qc_result.energy,
        coordinates=_coordinates(qc_result),
        symbols=_symbols(qc_result),
        artifacts=tuple(artifacts),
        provenance=provenance,
        payload=payload,
        metadata=metadata,
    )


# ── shielding table mapping (atom → {symbol, isotropic} key shape) ─────


def _map_shieldings(
    parsed: dict[object, object],
    base: int,
    requested: tuple[int, ...],
) -> tuple[dict[int, NmrShielding], list[str], list[str]]:
    """Map the parsed 0-based table onto declared-base payload keys.

    Returns ``(shieldings, missing, invalid)``: entries keyed by
    ``parsed_index + base``; *missing* lists requested atom indices (in the
    declared base) absent from the parsed table; *invalid* lists parsed
    entries without a usable ``{symbol, isotropic}`` shape.  When
    *requested* is empty every parsed entry is kept.
    """
    wanted: set[int] | None = None
    if requested:
        wanted = {int(index) for index in requested}

    shieldings: dict[int, NmrShielding] = {}
    invalid: list[str] = []
    for raw_index, entry in parsed.items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            invalid.append(str(raw_index))
            continue
        key = index + base
        if wanted is not None and key not in wanted:
            continue
        if not isinstance(entry, dict):
            invalid.append(str(raw_index))
            continue
        isotropic = entry.get("isotropic")
        try:
            isotropic_value = float(isotropic)
        except (TypeError, ValueError):
            invalid.append(str(raw_index))
            continue
        shieldings[key] = NmrShielding(
            symbol=str(entry.get("symbol", "") or ""),
            isotropic=isotropic_value,
        )

    missing: list[str] = []
    if wanted is not None:
        missing = [str(key) for key in sorted(wanted) if key not in shieldings]
    return shieldings, missing, invalid


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


__all__ = ["run_nmr_shielding"]
