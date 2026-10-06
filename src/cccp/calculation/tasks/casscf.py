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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


# ── shared science-completion validator (plan todo 11 / T10-D5) ─────────


@dataclass(frozen=True)
class CasscfCompletionVerdict:
    """Verdict of :func:`validate_casscf_completion`.

    ``reason`` is ``""`` when ``passed``; otherwise one of the stable
    tokens ``integrity_unverified`` / ``requested_artifacts_missing`` /
    ``cas_not_converged`` / ``convergence_fact_missing``.
    """

    passed: bool
    reason: str = ""


def extract_cas_convergence_fact(metadata: Mapping[str, Any] | None) -> bool | None:
    """Return the three-state CAS convergence fact from persisted metadata.

    Reads the production ``metadata["multireference"]["converged"]`` block
    first (the legacy-shaped record every CASSCF receipt/scientific record
    carries once produced by this task core), falling back to the raw
    ``metadata["casscf"]["converged"]`` parser block.  ``None`` means no
    convergence fact was recorded (old or foreign evidence) — never an
    implicit ``True``.
    """
    if not isinstance(metadata, Mapping):
        return None
    for key in ("multireference", "casscf"):
        block = metadata.get(key)
        if isinstance(block, Mapping) and "converged" in block:
            value = block.get("converged")
            if isinstance(value, bool):
                return value
    return None


def _rejudge_cas_log(log_path: Path) -> bool | None:
    """Re-judge convergence from the original log with the CURRENT parser.

    ``None`` means the log was unreadable — no evidence either way (the
    caller falls back to the recorded fact).  Truncated / incomplete logs
    parse to ``False`` (the parser never relaxes that rule).
    """
    # Lazy import: keep this module free of QC-interface load cost so the
    # task core and the ACP executor can import it cheaply.
    from cccp.qc.interfaces.orca import parse_casscf_output

    try:
        parsed = parse_casscf_output(log_path)
    except OSError:
        return None
    value = parsed.get("converged")
    return value if isinstance(value, bool) else None


def validate_casscf_completion(
    metadata: Mapping[str, Any] | None,
    *,
    artifact_paths: Sequence[Path | str] = (),
    log_path: Path | str | None = None,
    integrity_valid: bool = True,
    allow_log_rejudge: bool = False,
) -> CasscfCompletionVerdict:
    """The single CASSCF science-completion validator (plan todo 11).

    Invoked by the task core on fresh execution AND by both recovery
    entries of the ACP plan executor — the ``step_result.json`` adoption
    path and the ``scientific_result.json`` publication-retry path.  The
    executor only CALLS this validator; it never re-implements the science.

    Checks, in order:

    1. *integrity_valid* — the caller verified identity/digest (receipt
       ``step_identity`` + artifact sha256, or the record ``result_id``);
    2. every requested artifact in *artifact_paths* exists on disk;
    3. the CAS convergence fact: an explicit ``False`` never passes; an
       explicit ``True`` passes; an absent fact is re-judged from the
       original log ONLY when *allow_log_rejudge* (digest-verified log —
       receipts verified by ``verify_step_result``) and the log parses to
       converged under the current parser rules.  Anything else is refused
       so the caller conservatively recomputes.  Reading never rewrites
       historical task data.

    Args:
        metadata: Persisted task metadata (or ``None`` when absent).
        artifact_paths: Requested artifacts that must all be present.
        log_path: Candidate original CAS log for conservative re-judgement.
        integrity_valid: Caller-verified identity/digest outcome.
        allow_log_rejudge: Whether the log's digest/identity was verified
            (only the ``step_result.json`` adoption path can offer this).

    Returns:
        The verdict; ``passed=False`` carries a stable ``reason`` token.
    """
    if not integrity_valid:
        return CasscfCompletionVerdict(False, "integrity_unverified")
    for raw_path in artifact_paths:
        if not Path(raw_path).is_file():
            return CasscfCompletionVerdict(False, "requested_artifacts_missing")
    fact = extract_cas_convergence_fact(metadata)
    if fact is False:
        # An explicit non-convergence fact is conclusive: a log must never
        # "rescue" a receipt that recorded CAS non-convergence.
        return CasscfCompletionVerdict(False, "cas_not_converged")
    if allow_log_rejudge and log_path is not None:
        log = Path(log_path)
        if log.is_file():
            rejudged = _rejudge_cas_log(log)
            if rejudged is False:
                # Stale or absent fact re-judged against the original log
                # under the current parser rules.
                return CasscfCompletionVerdict(False, "cas_not_converged")
            if rejudged is True:
                return CasscfCompletionVerdict(True)
            # Unreadable log → no evidence; fall back to the recorded fact.
    if fact is True:
        return CasscfCompletionVerdict(True)
    return CasscfCompletionVerdict(False, "convergence_fact_missing")


def _completion_failure_message(reason: str) -> str:
    if reason == "requested_artifacts_missing":
        return "CASSCF required artifact missing after calculation"
    if reason == "convergence_fact_missing":
        return "CASSCF completion not proven: no CAS convergence fact recorded"
    if reason == "integrity_unverified":
        return "CASSCF completion not proven: identity/digest not verified"
    return "CASSCF calculation did not converge (CAS convergence not proven)"


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

    # Completion gate: backend success + energy is NOT enough — completed
    # only with an explicit CAS convergence fact AND the requested
    # artifacts present (shared validator, plan todo 11).
    required_artifacts: tuple[Path, ...] = (
        (target_dir / "active_space.json",) if target_dir is not None else ()
    )
    verdict = validate_casscf_completion(
        metadata, artifact_paths=required_artifacts, integrity_valid=True
    )
    if not verdict.passed:
        error_kind = (
            ErrorKind.BACKEND_FAILURE
            if verdict.reason == "requested_artifacts_missing"
            else ErrorKind.NOT_CONVERGED
        )
        return TaskResult(
            task=TaskKind.CASSCF,
            status="failed",
            complete=False,
            error_kind=error_kind,
            errors=(_completion_failure_message(verdict.reason),),
            energy_hartree=qc_result.energy,
            coordinates=_coordinates(qc_result),
            symbols=_symbols(qc_result),
            artifacts=tuple(artifacts),
            provenance=_provenance(backend_label, request),
            payload=None,
            metadata=metadata,
            converged=extract_cas_convergence_fact(metadata),
        )

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
        converged=True,
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
        # Production parser facts ride both channels identically; the
        # QCResult channel covers legacy/mock backends that only set the
        # envelope flag (never an implicit True: both default to False).
        "converged": bool(parsed.get("converged")) or bool(getattr(qc_result, "converged", False)),
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


__all__ = [
    "CasscfCompletionVerdict",
    "extract_cas_convergence_fact",
    "run_casscf",
    "validate_casscf_completion",
]
