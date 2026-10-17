"""xTB single-geometry constrained optimization task core.

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (typed constraint options → xcontrol
constraint objects + ``$fix`` freeze block) → interface execution (one xTB
``--opt`` process driven by ``.xcontrol``) → optional unrestrained physical
single point at the final geometry → typed
:class:`~cccp.calculation.results.XtbConstrainedOptPayload` plus log-parsed
convergence evidence.

Three-state semantics (``cccp.calculation.requests.P2_TASK_CONTRACTS``):
success = the xTB process ran and produced a log (geometry non-convergence
is recorded as ``converged_geometry=False`` evidence, mirroring xTB's
exit-0-but-unconverged behaviour); empty = no log produced is a failed
result with ``error_kind=backend_failure``; partial = n/a (the optional
physical SP is skipped, not partial, when no converged geometry exists).

The ``pes2ts_xtb_constrained_opt_request_v1`` legacy payload is mapped onto
:class:`~cccp.calculation.requests.XtbConstrainedOptOptions` by the ACP
adapter and is never parsed here.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from cccp.calculation._common import (
    backend_for_request,
    classify_failure,
    error_text,
    load_geometry,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.requests import (
    TaskKind,
    TaskRequest,
    XtbConstrainedOptOptions,
    validate_request,
)
from cccp.calculation.results import (
    ErrorKind,
    TaskResult,
    XtbConstrainedOptPayload,
)
from cccp.calculation.selection import precheck_runtime, select_semantic
from cccp.calculation.tasks.conformer_search import (
    artifact_ref,
    execution_provenance,
    resolve_operation,
)

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Backend execution method and its call shape (method name on the backend).
_CAPABILITY_METHODS: tuple[tuple[str, str], ...] = (
    ("constrained_optimize", "constrained"),
)

_OPT_LOG_NAME = "xtb.log"
_OPT_XYZ_NAME = "xtbopt.xyz"
_XCONTROL_NAME = ".xcontrol"
_SP_LOG_NAME = "xtb_sp.log"

# ── log parsing (convergence evidence) ──────────────────────────────────

_RE_GEOM_CONVERGED = re.compile(
    r"\*\*\*\s*GEOMETRY OPTIMIZATION CONVERGED AFTER\s+(\d+)\s+ITERATIONS\s*\*\*\*"
)
_RE_GEOM_FAILED = re.compile(
    r"\*\*\*\s*FAILED TO CONVERGE GEOMETRY OPTIMIZATION(?: IN\s+(\d+)\s+ITERATIONS)?\s*\*\*\*"
)
_RE_SCC_FAILED = re.compile(
    r"SCC.*(?:not converged|failed to converge|is not converged)", re.IGNORECASE
)
_RE_OPT_CYCLE = re.compile(r"^\s*\*\s*total energy\s*:", re.MULTILINE)
_RE_TOTAL_ENERGY = re.compile(r"\|\s*TOTAL ENERGY\s+([-+0-9.Ee]+)\s+Eh")
_RE_NORMAL_TERM = re.compile(r"(?<!ab)normal termination of xtb")


def parse_opt_log(log_text: str) -> dict[str, Any]:
    """Extract convergence/terminal/cost evidence from an xTB optimization log.

    ``success`` of the xTB subprocess is deliberately NOT inferred from the
    presence of ``xtbopt.xyz``: xTB writes the file and exits 0 even when the
    geometry optimization does not converge.
    """
    geom_conv = _RE_GEOM_CONVERGED.search(log_text)
    geom_fail = _RE_GEOM_FAILED.search(log_text)
    total_energy = _RE_TOTAL_ENERGY.search(log_text)
    energies = _RE_TOTAL_ENERGY.findall(log_text)
    n_cycles_matches = _RE_OPT_CYCLE.findall(log_text)
    scc_fail = _RE_SCC_FAILED.search(log_text)
    normal_term = _RE_NORMAL_TERM.search(log_text) is not None

    if geom_conv is not None:
        convergence_marker = (
            f"GEOMETRY_OPTIMIZATION_CONVERGED_AFTER_{geom_conv.group(1)}_ITERATIONS"
        )
        converged_geometry = True
        failed_to_converge = False
        n_geom_iterations = int(geom_conv.group(1))
    elif geom_fail is not None:
        iterations = geom_fail.group(1)
        convergence_marker = (
            f"FAILED_TO_CONVERGE_GEOMETRY_OPTIMIZATION_IN_{iterations}_ITERATIONS"
            if iterations
            else "FAILED_TO_CONVERGE_GEOMETRY_OPTIMIZATION"
        )
        converged_geometry = False
        failed_to_converge = True
        n_geom_iterations = int(iterations) if iterations else None
    else:
        convergence_marker = None
        converged_geometry = False
        failed_to_converge = False
        n_geom_iterations = None

    energy_effective = None
    if energies:
        try:
            energy_effective = float(energies[-1])
        except ValueError:
            energy_effective = None
    elif total_energy is not None:
        try:
            energy_effective = float(total_energy.group(1))
        except ValueError:
            energy_effective = None

    return {
        "converged_geometry": converged_geometry,
        "failed_to_converge": failed_to_converge,
        "convergence_marker": convergence_marker,
        "n_geometry_iterations": n_geom_iterations,
        "normal_termination": normal_term,
        "scc_failure_detected": scc_fail is not None,
        "energy_effective_hartree": energy_effective,
        "n_optimization_cycles_logged": len(n_cycles_matches) or None,
    }


def xcontrol_fix_block(fix_atoms: tuple[int, ...] | None) -> str:
    """Compile 0-based ``fix_atoms`` into an xcontrol ``$fix`` block.

    xTB expects 1-based indices; consecutive positions are compressed to
    ranges (``1-3, 6`` style).  Empty/None input yields an empty string.
    """
    if not fix_atoms:
        return ""
    sorted_atoms = sorted(set(int(a) + 1 for a in fix_atoms))
    parts: list[str] = []
    run_start = sorted_atoms[0]
    prev = run_start
    for atom in sorted_atoms[1:]:
        if atom == prev + 1:
            prev = atom
            continue
        parts.append(f"{run_start}-{prev}" if prev > run_start else f"{run_start}")
        run_start = prev = atom
    parts.append(f"{run_start}-{prev}" if prev > run_start else f"{run_start}")
    return f"$fix\n  atoms: {', '.join(parts)}\n$end\n"


def _constraint_objects(options: XtbConstrainedOptOptions) -> list[Any]:
    """Translation step: typed constraint options → interface constraint objects."""
    from cccp.qc.interfaces.constraints import (
        AngleConstraint,
        DihedralConstraint,
        DistanceConstraint,
    )

    objects: list[Any] = []
    for spec in options.constraints:
        force = options.force_constant
        if spec.kind == "distance":
            objects.append(
                DistanceConstraint(
                    atoms=(spec.atoms[0], spec.atoms[1]),
                    target=spec.target,
                    force_constant=force,
                )
            )
        elif spec.kind == "angle":
            objects.append(
                AngleConstraint(
                    atoms=(spec.atoms[0], spec.atoms[1], spec.atoms[2]),
                    target=spec.target,
                    force_constant=force,
                )
            )
        else:
            objects.append(
                DihedralConstraint(
                    atoms=(spec.atoms[0], spec.atoms[1], spec.atoms[2], spec.atoms[3]),
                    target=spec.target,
                    force_constant=force,
                )
            )
    return objects


def run_xtb_constrained_opt(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one xTB native constrained optimization (+ optional physical SP).

    A normally-terminated xTB run whose geometry optimization did not
    converge is still a completed result: the evidence records
    ``converged_geometry=False`` and the optional physical single point is
    skipped (``skipped_reason="no_converged_geometry_available"``).
    """
    validate_request(request)
    if request.task is not TaskKind.XTB_CONSTRAINED_OPT:
        message = (
            f"run_xtb_constrained_opt requires task 'xtb_constrained_opt', "
            f"got {request.task.value!r}"
        )
        raise ValueError(message)
    if not isinstance(request.options, XtbConstrainedOptOptions):
        message = "xtb_constrained_opt requires XtbConstrainedOptOptions"
        raise ValueError(message)
    options = request.options
    ctx = resolve_context(request, context)

    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:  # pragma: no cover - validate_request enforces this
        message = "xtb_constrained_opt requires a structure input"
        raise ValueError(message)
    path = structure.path
    if path is not None and not path.is_absolute():
        path = ctx.input_root() / path
    coordinates, symbols = load_geometry(
        path=path,
        coordinates=structure.coordinates,
        symbols=structure.symbols,
        elements=structure.elements,
    )

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
    opt_dir = target_dir / "opt"
    sp_dir = target_dir / "sp"
    opt_dir.mkdir(parents=True, exist_ok=True)

    timeout = (
        request.resources.timeout_s if request.resources.timeout_s is not None else ctx.timeout_s
    )
    timeout_arg = int(timeout) if timeout is not None else None
    extra_control_text = xcontrol_fix_block(options.fix_atoms)

    _, _, operation = resolve_operation(backend, _CAPABILITY_METHODS)
    try:
        t0 = time.perf_counter()
        opt_result = operation(
            coordinates,
            list(symbols),
            output_dir=opt_dir,
            constraints=_constraint_objects(options),
            charge=request.charge,
            multiplicity=request.multiplicity,
            opt_level=options.opt_level,
            max_steps=options.max_steps,
            timeout=timeout_arg,
            extra_control_text=extra_control_text,
        )
        opt_wall = time.perf_counter() - t0
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.XTB_CONSTRAINED_OPT,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=execution_provenance(backend_label, request),
        )

    opt_log_path = opt_dir / _OPT_LOG_NAME
    if not opt_log_path.is_file():
        return TaskResult(
            task=TaskKind.XTB_CONSTRAINED_OPT,
            status="failed",
            complete=False,
            error_kind=ErrorKind.BACKEND_FAILURE,
            errors=(
                str(
                    getattr(opt_result, "error_message", None)
                    or "xTB produced no optimization log"
                ),
            ),
            provenance=execution_provenance(backend_label, request),
        )

    log_text = opt_log_path.read_text(encoding="utf-8", errors="replace")
    opt_evidence = parse_opt_log(log_text)

    final_coords = None
    if (
        getattr(opt_result, "success", False)
        and getattr(opt_result, "coordinates", None) is not None
    ):
        final_coords = getattr(opt_result, "coordinates")

    # Physical single point (no restraint potential) at the final geometry.
    sp_evidence: dict[str, Any] | None = None
    sp_log_path: Path | None = None
    if options.physical_sp and final_coords is not None:
        sp_dir.mkdir(parents=True, exist_ok=True)
        sp_operation = getattr(backend, "single_point", None)
        if callable(sp_operation):
            t_sp = time.perf_counter()
            sp_result = sp_operation(
                final_coords,
                list(symbols),
                output_dir=sp_dir,
                charge=request.charge,
                multiplicity=request.multiplicity,
                timeout=timeout_arg,
            )
            sp_wall = time.perf_counter() - t_sp
            sp_log_path = sp_dir / _SP_LOG_NAME
            sp_text = (
                sp_log_path.read_text(encoding="utf-8", errors="replace")
                if sp_log_path.is_file()
                else ""
            )
            sp_normal = _RE_NORMAL_TERM.search(sp_text) is not None
            sp_energies = _RE_TOTAL_ENERGY.findall(sp_text)
            energy_physical = None
            if sp_energies:
                try:
                    energy_physical = float(sp_energies[-1])
                except ValueError:
                    energy_physical = None
            if getattr(sp_result, "energy", None) is not None:
                energy_physical = float(getattr(sp_result, "energy"))
            sp_evidence = {
                "requested": True,
                "success": bool(getattr(sp_result, "success", False)),
                "energy_physical_hartree": energy_physical,
                "normal_termination": sp_normal,
                "scc_failure_detected": _RE_SCC_FAILED.search(sp_text) is not None
                if sp_text
                else None,
                "wall_seconds": sp_wall,
            }
        else:  # pragma: no cover - xtb backend always provides single_point
            sp_evidence = {
                "requested": True,
                "success": False,
                "energy_physical_hartree": None,
                "skipped_reason": "backend_has_no_single_point",
            }
    elif options.physical_sp:
        sp_evidence = {
            "requested": True,
            "success": False,
            "energy_physical_hartree": None,
            "skipped_reason": "no_converged_geometry_available",
        }

    artifacts = []
    for name, artifact_type in (
        (_OPT_LOG_NAME, "log"),
        (_OPT_XYZ_NAME, "structure"),
        (_XCONTROL_NAME, "input"),
    ):
        candidate = opt_dir / name
        if candidate.is_file():
            artifacts.append(artifact_ref(candidate, artifact_type, backend_label))
    if sp_log_path is not None and sp_log_path.is_file():
        artifacts.append(artifact_ref(sp_log_path, "log", backend_label))
    optimized_ref = (
        artifacts[1] if len(artifacts) > 1 and artifacts[1].type == "structure" else None
    )

    payload = XtbConstrainedOptPayload(
        optimized_ref=optimized_ref,
        energy_effective_hartree=opt_evidence["energy_effective_hartree"],
        energy_physical_hartree=(
            sp_evidence.get("energy_physical_hartree") if sp_evidence else None
        ),
        converged_geometry=opt_evidence["converged_geometry"],
    )
    metadata: dict[str, Any] = {
        "opt": dict(opt_evidence),
        "opt_dir": str(opt_dir),
        "sp_dir": str(sp_dir),
        "opt_wall_seconds": opt_wall,
        "timeout_applied_seconds": timeout_arg,
        "opt_level": options.opt_level,
        "gfn_level": options.gfn_level,
        "max_steps": options.max_steps,
        "fix_atoms": list(options.fix_atoms) if options.fix_atoms is not None else None,
        "n_constraints": len(options.constraints),
        "physical_sp": sp_evidence,
    }

    return TaskResult(
        task=TaskKind.XTB_CONSTRAINED_OPT,
        status="completed",
        complete=True,
        errors=(),
        energy_hartree=opt_evidence["energy_effective_hartree"],
        coordinates=final_coords,
        symbols=tuple(str(s) for s in symbols),
        converged=opt_evidence["converged_geometry"],
        artifacts=tuple(artifacts),
        provenance=execution_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


__all__ = [
    "parse_opt_log",
    "run_xtb_constrained_opt",
    "xcontrol_fix_block",
]
