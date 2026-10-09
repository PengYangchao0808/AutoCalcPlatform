"""Two-step backend selection: semantic capability resolution + runtime precheck.

Step ① (:func:`select_semantic`) maps the *scientific* request — task kind,
typed options, structure role, method/electronic-state requirements — onto a
required capability and constraints, then picks an implementing backend from
the declaration matrix (:mod:`cccp.backends.matrix`) via the deterministic
capability priority order.  Step ② (:func:`precheck_runtime`) checks the
selected backend's required programs/binaries against **the context passed to
this call** and nothing else.

Contract rules implemented here:

* Explicit requests are honored strictly: an explicit ORCA request whose ORCA
  program is missing raises :class:`BackendUnavailableError` — the selector
  NEVER silently substitutes another declaring backend (that would change the
  scientific model).
* Runtime precheck consumes only ``context.config`` (the caller's already
  resolved config).  This module never reads global/default configuration
  (no ``load_config``/``_get_default_config`` fallback): when a context pins
  ``executables.<name>.path`` the pin is authoritative and checked strictly
  (no PATH/env fallback for that program); when no pin is present the
  environment chain of :func:`cccp.software.resolve_executable` applies.
* ``context=None`` (or ``config=None``) is supported and documented: it means
  "no configured program pins" — availability is judged from the environment
  chain only.  It is not a licence to read default config.
* The returned :class:`BackendSelection` record carries backend, capability,
  reason, final parameters and required programs with availability for
  provenance/debugging/tests.

Selection is declaration-driven and deterministic; the runtime precheck
validates the *selected* backend and does not re-select on missing binaries
(alternatives stay visible in ``candidates`` for the caller to decide
explicitly).  This module maps capabilities only — it does **not** make tasks
executable (the task→execution table is completed by later todos).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from cccp.backends.matrix import (
    CAPABILITY_BACKEND_PRIORITY,
    CAPABILITY_MATRIX,
    TASK_CAPABILITY_MAP,
    BackendCapabilityStatus,
    normalize_capability_name,
)
from cccp.backends.registry import backend_registry
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import (
    DynamicCorrelation,
    JsonObject,
    OptimizationMode,
    StructureRole,
)
from cccp.calculation.errors import (
    BackendUnavailableError,
    TaskInputError,
    UnsupportedCapabilityError,
)
from cccp.calculation.requests import (
    CasscfOptions,
    OptimizeOptions,
    ScanMode,
    ScanOptions,
    TaskKind,
    TaskRequest,
    validate_request,
)
from cccp.software import resolve_executable

logger = logging.getLogger(__name__)

#: Backend → required software keys (``cccp.software`` names).
BACKEND_SOFTWARE: dict[str, tuple[str, ...]] = {
    "orca": ("orca",),
    "xtb": ("xtb",),
    "crest": ("crest",),
    "censo": ("censo",),
    "molclus": ("molclus",),
    "isostat": ("isostat",),
    "external": (),
}

#: Per-(backend, capability) software overrides (e.g. the ``external``
#: backend drives different tools per capability).
CAPABILITY_SOFTWARE: dict[tuple[str, str], tuple[str, ...]] = {
    ("external", "clustering"): ("isostat",),
    ("external", "thermochemistry"): ("shermo",),
}


@dataclass(frozen=True, slots=True)
class ProgramRequirement:
    """One required program for a selection, with its precheck outcome.

    ``available is None`` means the runtime precheck has not run yet (the
    step-① record lists names only); ``True``/``False`` is the judgment of
    the last :func:`precheck_runtime` call for the context it received.
    """

    name: str
    configured_path: str | None = None
    resolved_path: str | None = None
    available: bool | None = None
    source: str = ""

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"name": self.name, "source": self.source}
        if self.configured_path is not None:
            payload["configured_path"] = self.configured_path
        if self.resolved_path is not None:
            payload["resolved_path"] = self.resolved_path
        payload["available"] = self.available
        return payload


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """Derived capability demand of one request (step-① input to selection)."""

    task: TaskKind | None
    capability: str
    also_required: tuple[str, ...] = ()
    constraints: tuple[tuple[str, str], ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class BackendSelection:
    """Selection record: backend / capability / reason / params / programs."""

    task: TaskKind | None
    capability: str
    also_required: tuple[str, ...]
    backend: str
    reason: str
    params: JsonObject = field(default_factory=dict)
    required_programs: tuple[ProgramRequirement, ...] = ()
    explicit_backend: bool = False
    candidates: tuple[str, ...] = ()
    runtime_checked: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params))

    def to_dict(self) -> JsonObject:
        """Serialise the full selection record for provenance/debugging."""
        return {
            "task": self.task.value if self.task is not None else None,
            "capability": self.capability,
            "also_required": list(self.also_required),
            "backend": self.backend,
            "reason": self.reason,
            "params": dict(self.params),
            "required_programs": [program.to_dict() for program in self.required_programs],
            "explicit_backend": self.explicit_backend,
            "candidates": list(self.candidates),
            "runtime_checked": self.runtime_checked,
        }


# ── step ①: semantic selection ──────────────────────────────────────────


def _canonical_capability(name: str) -> str:
    try:
        return normalize_capability_name(name)
    except ValueError as exc:
        raise UnsupportedCapabilityError(str(exc)) from exc


def _scan_is_constrained(options: ScanOptions | None) -> bool:
    """True when the scan plan carries constraints beyond one drive coordinate.

    A multi-coordinate plan constrains every coordinate at each frame (the
    synchronous-constraint semantics of ``relaxed_scan``), which is the
    constrained variant of the capability.
    """
    return options is not None and len(options.coordinates) > 1


def capability_requirement(request: TaskRequest) -> CapabilityRequirement:
    """Derive the required capability and constraints from a request.

    This is not a plain task→backend map: the scientific options decide which
    capability the task needs (e.g. optimize by structure role needs
    ``optimize``-family or ``transition_state``; scan with constraints needs
    the constrained variant; CASSCF with dynamic correlation additionally
    needs ``nevpt2``).

    Raises:
        TaskInputError: On an invalid envelope (``validate_request``).
        UnsupportedCapabilityError: When the derived capability name is
            unknown to the declaration vocabulary.
    """
    validate_request(request)
    options = request.options
    constraints: list[tuple[str, str]] = []
    if request.electronic_state is not None:
        constraints.append(("electronic_state", request.electronic_state.state_id))
    also_required: tuple[str, ...] = ()

    if request.task is TaskKind.OPTIMIZE:
        opt = options if isinstance(options, OptimizeOptions) else None
        mode = opt.mode if opt is not None else OptimizationMode.UNCONSTRAINED
        ts_requested = (
            mode is OptimizationMode.TRANSITION_STATE
            or (opt is not None and opt.ts is not None and opt.ts.enabled)
            or (
                request.structure is not None
                and request.structure.role is StructureRole.TRANSITION_STATE
            )
        )
        constraints.append(("optimization_mode", mode.value))
        if request.structure is not None:
            constraints.append(("structure_role", request.structure.role.value))
        if ts_requested:
            capability = "transition_state"
        elif mode is OptimizationMode.CONSTRAINED:
            capability = "constrained_optimization"
        else:
            capability = "geometry_optimization"
    elif request.task is TaskKind.SCAN:
        scan = options if isinstance(options, ScanOptions) else None
        mode = scan.mode if scan is not None else ScanMode.RELAXED
        coordinate_count = len(scan.coordinates) if scan is not None else 0
        constraints.append(("scan_mode", mode.value))
        constraints.append(("scan_coordinates", str(coordinate_count)))
        if mode is not ScanMode.RELAXED:
            capability = "rigid_scan"
        elif _scan_is_constrained(scan):
            capability = "constrained_relaxed_scan"
        else:
            capability = "relaxed_scan"
    elif request.task is TaskKind.CASSCF:
        capability = "casscf"
        if isinstance(options, CasscfOptions):
            correlation = options.spec.dynamic_correlation
            constraints.append(("dynamic_correlation", correlation.value))
            if correlation is not DynamicCorrelation.NONE:
                also_required = ("nevpt2",)
    else:
        base = {
            TaskKind.SINGLEPOINT: "single_point",
            TaskKind.FREQUENCY: "frequency",
            TaskKind.IRC: "irc",
            TaskKind.THERMOCHEMISTRY: "thermochemistry",
            TaskKind.CONFORMER_SEARCH: "conformer_search",
            TaskKind.MD_SAMPLING: "md_sampling",
            TaskKind.CLUSTERING: "clustering",
            TaskKind.CENSO_REFINE: "censo_refine",
            TaskKind.NMR_SHIELDING: "nmr_shielding",
            TaskKind.XTB_PATH_SEARCH: "xtb_path_search",
            TaskKind.ORCA_GRADIENT: "orca_gradient",
        }.get(request.task)
        if base is None:
            message = f"no capability mapping for task {request.task.value!r}"
            raise UnsupportedCapabilityError(message)
        capability = base

    canonical = _canonical_capability(capability)
    also_required = tuple(_canonical_capability(name) for name in also_required)
    vocabulary = TASK_CAPABILITY_MAP.get(request.task.value, ())
    if canonical not in vocabulary and request.task is not None:
        message = (
            f"derived capability '{canonical}' is not in the declared mapping "
            f"for task '{request.task.value}' ({', '.join(vocabulary) or 'empty'})"
        )
        raise UnsupportedCapabilityError(message)
    reason = (
        f"task '{request.task.value}' requires capability '{canonical}'"
        + (f" plus '{', '.join(also_required)}'" if also_required else "")
        + (f" [{', '.join(f'{k}={v}' for k, v in constraints)}]" if constraints else "")
    )
    return CapabilityRequirement(
        task=request.task,
        capability=canonical,
        also_required=also_required,
        constraints=tuple(constraints),
        reason=reason,
    )


def _final_params(request: TaskRequest | None, requirement: CapabilityRequirement) -> JsonObject:
    params: JsonObject = {
        "capability": requirement.capability,
        "also_required": list(requirement.also_required),
    }
    for key, value in requirement.constraints:
        params[key] = value
    if request is None:
        return params
    params["task"] = request.task.value
    params["method"] = request.level.method
    params["basis"] = request.level.basis
    params["charge"] = request.charge
    params["multiplicity"] = request.multiplicity
    for key, value in (
        ("dispersion", request.level.dispersion),
        ("solvent", request.level.solvent),
        ("solvent_model", request.level.solvent_model),
        ("integration_grid", request.level.integration_grid),
        ("scf", request.level.scf),
        ("ri_approximation", request.level.ri_approximation),
        ("auxiliary_basis_j", request.level.auxiliary_basis_j),
        ("auxiliary_basis_c", request.level.auxiliary_basis_c),
    ):
        if value is not None:
            params[key] = value
    if request.electronic_state is not None:
        params["electronic_state"] = {
            "state_id": request.electronic_state.state_id,
            "target_multiplicity": request.electronic_state.target_multiplicity,
            "spin_mode": request.electronic_state.spin_mode.value,
        }
    return params


def _required_software(backend: str, capability: str) -> tuple[str, ...]:
    override = CAPABILITY_SOFTWARE.get((backend, capability))
    if override is not None:
        return override
    return BACKEND_SOFTWARE.get(backend, ())


def _implementing_backends(capability: str, also_required: Sequence[str]) -> list[str]:
    needed = (capability, *also_required)
    registered = {name for name, _ in backend_registry.list_all()}
    return [
        name
        for name, row in sorted(CAPABILITY_MATRIX.items())
        if name in registered
        and all(row.get(cap) is BackendCapabilityStatus.AVAILABLE for cap in needed)
    ]


def _ordered_candidates(candidates: Sequence[str], capability: str) -> list[str]:
    priority = CAPABILITY_BACKEND_PRIORITY.get(capability, ())
    ordered = [name for name in priority if name in candidates]
    ordered.extend(sorted(name for name in candidates if name not in priority))
    return ordered


def _select(requirement: CapabilityRequirement, explicit_backend: str | None) -> BackendSelection:
    capability = requirement.capability
    also = requirement.also_required
    candidates = _implementing_backends(capability, also)
    explicit = explicit_backend is not None
    if explicit:
        name = explicit_backend.strip().lower()
        if name not in CAPABILITY_MATRIX:
            known = ", ".join(sorted(CAPABILITY_MATRIX))
            raise TaskInputError(f"Unknown backend: {explicit_backend!r}. Known: {known}")
        row = CAPABILITY_MATRIX[name]
        for needed in (capability, *also):
            if row.get(needed) is not BackendCapabilityStatus.AVAILABLE:
                declared = row.get(needed)
                status = declared.value if declared is not None else "undeclared"
                message = (
                    f"Backend {name!r} does not implement capability {needed!r} "
                    f"(declared: {status}); refusing to substitute another backend"
                )
                raise UnsupportedCapabilityError(message)
        selected = name
        reason = requirement.reason + f"; backend {name!r} explicitly requested and declares it"
    else:
        ordered = _ordered_candidates(candidates, capability)
        if not ordered:
            available = ", ".join(sorted(CAPABILITY_MATRIX)) or "none"
            message = (
                f"No registered backend implements capability {capability!r}"
                + (f" (+ {', '.join(also)})" if also else "")
                + f". Known backends: {available}"
            )
            raise UnsupportedCapabilityError(message)
        selected = ordered[0]
        reason = requirement.reason + (
            f"; selected backend {selected!r} by capability priority among {list(ordered)}"
        )
    programs = tuple(
        ProgramRequirement(name=name, source="unchecked")
        for name in _required_software(selected, capability)
    )
    logger.debug("semantic selection: %s", reason)
    return BackendSelection(
        task=requirement.task,
        capability=capability,
        also_required=also,
        backend=selected,
        reason=reason,
        params=_final_params(None, requirement),
        required_programs=programs,
        explicit_backend=explicit,
        candidates=tuple(candidates),
        runtime_checked=False,
    )


def select_semantic(request: TaskRequest) -> BackendSelection:
    """Step ①: derive the capability demand and pick a declaring backend.

    Pure declaration-level selection — no binaries are probed here.  The
    record's ``required_programs`` list the software names this capability
    needs with ``available=None`` (unchecked) until :func:`precheck_runtime`.
    """
    requirement = capability_requirement(request)
    selection = _select(requirement, request.backend)
    return replace(selection, params=_final_params(request, requirement))


def select_capability(
    capability: str,
    *,
    backend: str | None = None,
    also_required: Sequence[str] = (),
    task: TaskKind | None = None,
) -> BackendSelection:
    """Step ① for a raw capability name (P2 seam; used by P2 selection tests).

    Raises:
        UnsupportedCapabilityError: Unknown capability name, or no backend
            declares it implemented.
        TaskInputError: Unknown explicit backend name.
    """
    requirement = CapabilityRequirement(
        task=task,
        capability=_canonical_capability(capability),
        also_required=tuple(_canonical_capability(name) for name in also_required),
        reason=f"capability {_canonical_capability(capability)!r} requested directly",
    )
    return _select(requirement, backend)


# ── step ②: runtime precheck (this call's context only) ─────────────────


def _context_config(context: TaskContext | None) -> Mapping[str, object]:
    if context is None or context.config is None:
        return {}
    return context.config


def _configured_pin(config: Mapping[str, object], name: str) -> str | None:
    executables = config.get("executables")
    if not isinstance(executables, Mapping):
        return None
    entry = executables.get(name)
    if not isinstance(entry, Mapping):
        return None
    path = entry.get("path")
    if path is None or isinstance(path, bool):
        return None
    text = str(path).strip()
    return text or None


def _check_program(name: str, config: Mapping[str, object]) -> ProgramRequirement:
    """Judge one program against THIS call's config only.

    A pin (``executables.<name>.path``) is authoritative: it is checked
    strictly as an executable file and never rescued by PATH/env/scan.
    Without a pin the environment chain of ``resolve_executable`` applies.
    """
    pin = _configured_pin(config, name)
    if pin is not None:
        candidate = Path(pin).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return ProgramRequirement(
                name=name,
                configured_path=pin,
                resolved_path=str(candidate.resolve()),
                available=True,
                source="config",
            )
        return ProgramRequirement(
            name=name,
            configured_path=pin,
            resolved_path=None,
            available=False,
            source="config",
        )
    resolved = resolve_executable(name)
    if resolved is not None:
        return ProgramRequirement(
            name=name,
            configured_path=None,
            resolved_path=str(resolved),
            available=True,
            source="environment",
        )
    return ProgramRequirement(
        name=name,
        configured_path=None,
        resolved_path=None,
        available=False,
        source="environment",
    )


def precheck_runtime(
    selection: BackendSelection,
    context: TaskContext | None = None,
) -> BackendSelection:
    """Step ②: check the selected backend's programs against *context*.

    Consumes only the passed ``context`` (its already-resolved config with
    program paths); it never reads global/default configuration — a missing
    binary in THIS context raises even when some other config or PATH entry
    would provide one.

    Raises:
        BackendUnavailableError: When a required program is missing in this
            context (explicit ORCA without ORCA surfaces here — never as a
            silent switch to another backend).
    """
    config = _context_config(context)
    checked = tuple(_check_program(program.name, config) for program in selection.required_programs)
    missing = [program for program in checked if program.available is False]
    if missing:
        details = "; ".join(
            (
                f"{program.name!r} "
                + (
                    f"(configured path: {program.configured_path})"
                    if program.configured_path
                    else "(environment resolution)"
                )
            )
            for program in missing
        )
        message = (
            f"Backend {selection.backend!r} requires program(s) for capability "
            f"{selection.capability!r} that are not available in this context: {details}"
        )
        raise BackendUnavailableError(message)
    return replace(selection, required_programs=checked, runtime_checked=True)


def select_backend(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> BackendSelection:
    """Two-step selection in one call: semantic step + runtime precheck.

    Equivalent to ``precheck_runtime(select_semantic(request), context)``.
    With ``context=None`` availability is judged from the environment chain
    only (no configured pins, no default-config reread).
    """
    return precheck_runtime(select_semantic(request), context)


__all__ = [
    "BACKEND_SOFTWARE",
    "CAPABILITY_SOFTWARE",
    "BackendSelection",
    "CapabilityRequirement",
    "ProgramRequirement",
    "capability_requirement",
    "precheck_runtime",
    "select_backend",
    "select_capability",
    "select_semantic",
]
