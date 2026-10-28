"""Bind ACP2 science capabilities to the existing CCCP task API (A-06a).

A capability is a scientific statement of *what* is computed; the fifteen
existing :class:`cccp.calculation.requests.TaskKind` task cores are the only
execution station.  This module converts capability inputs into the
authoritative :class:`~cccp.calculation.requests.TaskRequest` (schema version
1, existing option/payload types) and delegates exactly once:

* ``electronic.energy`` -> ``cccp.calculation.tasks.singlepoint.run_singlepoint``;
* ``geometry.optimize`` -> ``cccp.calculation.tasks.optimize.run_optimize``;
* ``vibration.compute`` -> ``cccp.calculation.tasks.frequency.run_frequency``;
* ``nmr.shielding`` -> ``cccp.calculation.tasks.nmr_shielding.run_nmr_shielding``;
* ``vibration.analyze`` -> the existing pure-analysis entry point
  :func:`cccp.analysis.frequency_products.build_frequency_report`;
* ``nmr.compare`` -> the existing pure-analysis entry point
  :func:`cccp.analysis.nmr.comparison.compare_nmr_shielding`.

It never implements a calculation, never adds a second executor or dispatcher
and never degrades an unsupported capability into a different task.  A caller
may additionally bind a direct reference to any of the fifteen existing task
kinds (the frozen inventory is
:data:`cccp.calculation.capabilities.contracts.TASK_KINDS`), so a recipe step
can name an existing task kind without inventing a capability.  Direct
references outside the three seeded compute kinds are bindable for planning
but are not executable here: :meth:`TaskBinding.execute` raises the typed
:class:`UnsupportedBindingError` instead of inventing a route.

Preflight is deliberately side-effect free: it never starts a subprocess,
creates an artifact directory, writes a job/task file or loads a plugin.  It
validates JSON structure through ``TaskRequest.from_dict`` plus CCCP
scientific semantics only (structure, charge/multiplicity, electronic state,
method/basis, units and applicability).  Platform constraints — permission,
queue, account, budget — belong to Runtime and are not modelled here.

Task modules are imported lazily inside the dispatch function, so importing
the capability catalog (or calling :func:`preflight`) never imports
:mod:`cccp.calculation.tasks`.

Author: QCcalc Team
"""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from cccp.calculation.capabilities.builtin import builtin_registry
from cccp.calculation.capabilities.contracts import (
    TASK_KINDS,
    CapabilityDescriptor,
    CapabilityKind,
)
from cccp.calculation.capabilities.registry import CapabilityRegistry
from cccp.calculation.contracts import OptimizationMode, StructureRole, electron_parity_ok
from cccp.calculation.errors import CalculationError
from cccp.calculation.requests import (
    TASK_REQUEST_SCHEMA_VERSION,
    OptimizeOptions,
    TaskKind,
    TaskRequest,
)
from cccp.utils.constants import ATOMIC_NUMBER

__all__ = [
    "BINDABLE_TASK_KINDS",
    "EXISTING_TASK_KINDS",
    "REQUEST_CONTRACT_REF",
    "RESULT_CONTRACT_REF",
    "SUPPORTED_COMPUTE_TASK_KINDS",
    "TASK_BINDING_CONTRACT_VERSION",
    "TASK_BINDING_SCHEMA",
    "TASK_BINDING_SCHEMA_VERSION",
    "TASK_ENTRYPOINTS",
    "BindingSelection",
    "BindingSelectionMode",
    "PreflightCheck",
    "PreflightOutcome",
    "PreflightResult",
    "ScientificPreconditionError",
    "TaskBinding",
    "TaskBindingError",
    "TaskEntrypoint",
    "TaskKindReference",
    "UnknownTaskKindError",
    "UnsupportedBindingError",
    "bind_capability",
    "bind_task_kind",
    "build_task_request",
    "execute_binding",
    "preflight_binding",
]

#: Contract identity frozen by ``task-binding.schema.json``.
TASK_BINDING_SCHEMA = "acp2.science.task_binding"
TASK_BINDING_SCHEMA_VERSION = 1
TASK_BINDING_CONTRACT_VERSION = "acp2.science.task-binding.v1"

#: The authoritative existing contracts, referenced but never redefined.
REQUEST_CONTRACT_REF = "cccp.calculation.requests#TaskRequest"
RESULT_CONTRACT_REF = "cccp.calculation.results#TaskResult"
_REQUEST_VALIDATED_BY = "cccp.calculation.requests.TaskRequest.from_dict"
_ERROR_SCHEMA_REF = "https://acp.local/acp2/science/errors.schema.json#/$defs/errorRecord"

#: The fifteen existing task kinds, re-exported from the frozen contract.
EXISTING_TASK_KINDS = TASK_KINDS
BINDABLE_TASK_KINDS = TASK_KINDS

_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class BindingSelectionMode(str, Enum):
    """How a binding selected its implementation."""

    CAPABILITY = "capability"
    EXISTING_TASK_KIND = "existing_task_kind"


class PreflightOutcome(str, Enum):
    """The three preflight outcomes declared by the frozen schema."""

    READY = "ready"
    NOT_READY = "not_ready"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


# ---------------------------------------------------------------------------
# Typed binding errors
# ---------------------------------------------------------------------------


class TaskBindingError(CalculationError):
    """Base class for typed task-binding errors.

    The error record carries the frozen ``errors.schema.json`` fields, so a
    caller can surface ``code``/``layer``/``detail`` without reinterpreting
    exception messages.
    """

    code = "invalid_scientific_input"
    layer = "scientific"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        """Create the error with structured detail.

        Args:
            message: Human-readable message.
            detail: Structured detail required by the frozen error schema.
        """
        super().__init__(message)
        self.detail = dict(detail or {})

    def to_error_record(self) -> dict[str, object]:
        """Return the schema-shaped error record."""
        return {
            "code": self.code,
            "layer": self.layer,
            "retryable": self.retryable,
            "detail": dict(self.detail),
            "detail_required": True,
            "message": str(self),
        }


class UnknownTaskKindError(TaskBindingError, LookupError):
    """A direct task-kind reference is not one of the fifteen task kinds."""

    code = "unknown_implementation"

    def __init__(self, task_kind: str) -> None:
        """Create the error for *task_kind*."""
        super().__init__(
            f"task kind {task_kind!r} is not one of the fifteen existing task kinds",
            detail={
                "capability_id": "<task_kind_reference>",
                "implementation_id": str(task_kind),
            },
        )


class UnsupportedBindingError(TaskBindingError, LookupError):
    """A selected capability has no implemented single task delegation."""

    code = "unknown_implementation"

    def __init__(
        self,
        message: str,
        *,
        capability_id: str | None,
        implementation_id: str | None,
    ) -> None:
        """Create the error naming the unsupported selection."""
        super().__init__(
            message,
            detail={
                "capability_id": capability_id or "<task_kind_reference>",
                "implementation_id": implementation_id or "<unimplemented>",
            },
        )


class ScientificPreconditionError(TaskBindingError, ValueError):
    """A CCCP scientific precondition failed before any execution."""

    code = "invalid_scientific_input"

    @classmethod
    def from_check(cls, check: PreflightCheck) -> ScientificPreconditionError:
        """Build the error from a failed :class:`PreflightCheck`."""
        return cls(
            check.reason,
            detail={
                "field": check.id,
                "reason": check.reason,
                "expected": check.expected,
                "actual": check.actual,
            },
        )


# ---------------------------------------------------------------------------
# Entry-point table (three compute cores; analysis stays separate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskEntrypoint:
    """One existing ``run_*`` task core reachable from a binding."""

    task_kind: str
    module: str
    function: str

    @property
    def target(self) -> str:
        """Return the delegation target recorded in binding metadata."""
        return f"cccp.calculation.{self.function}"


#: The compute capabilities with an implemented single-task delegation; no
#: other task kind is executable through this binding.  Direct references to
#: the remaining task kinds are bindable for planning but not executable here.
TASK_ENTRYPOINTS: Mapping[str, TaskEntrypoint] = MappingProxyType(
    {
        "singlepoint": TaskEntrypoint(
            task_kind="singlepoint",
            module="cccp.calculation.tasks.singlepoint",
            function="run_singlepoint",
        ),
        "optimize": TaskEntrypoint(
            task_kind="optimize",
            module="cccp.calculation.tasks.optimize",
            function="run_optimize",
        ),
        "frequency": TaskEntrypoint(
            task_kind="frequency",
            module="cccp.calculation.tasks.frequency",
            function="run_frequency",
        ),
        "nmr_shielding": TaskEntrypoint(
            task_kind="nmr_shielding",
            module="cccp.calculation.tasks.nmr_shielding",
            function="run_nmr_shielding",
        ),
    }
)

SUPPORTED_COMPUTE_TASK_KINDS: tuple[str, ...] = tuple(TASK_ENTRYPOINTS)

#: The pure-analysis targets; they are never tasks and never start a subprocess.
_ANALYSIS_DELEGATION_TARGETS: Mapping[str, str] = MappingProxyType(
    {
        "vibration.analyze": "cccp.analysis.frequency_products.build_frequency_report",
        "nmr.compare": "cccp.analysis.nmr.comparison.compare_nmr_shielding",
        "thermochemistry.ensemble": (
            "cccp.analysis.ensemble.science.build_ensemble_thermochemistry"
        ),
    }
)


# ---------------------------------------------------------------------------
# Selection and preflight values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskKindReference:
    """Typed reference to one of the fifteen existing task kinds."""

    kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in TASK_KINDS:
            raise UnknownTaskKindError(self.kind)
        object.__setattr__(self, "kind", self.kind)

    def __str__(self) -> str:
        """Return the task-kind id."""
        return self.kind


@dataclass(frozen=True)
class BindingSelection:
    """What a binding selects: a capability or an existing task kind."""

    mode: BindingSelectionMode | str
    capability_id: str | None = None
    task_kind: str | None = None

    def __post_init__(self) -> None:
        try:
            mode = BindingSelectionMode(self.mode)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in BindingSelectionMode)
            raise ScientificPreconditionError(
                f"selection.mode must be one of: {allowed}",
                detail={
                    "field": "selection.mode",
                    "reason": "unknown selection mode",
                    "expected": allowed,
                    "actual": self.mode,
                },
            ) from exc
        object.__setattr__(self, "mode", mode)
        if mode is BindingSelectionMode.CAPABILITY:
            if not isinstance(self.capability_id, str) or not _CAPABILITY_ID_RE.fullmatch(
                self.capability_id
            ):
                raise ScientificPreconditionError(
                    "a capability selection requires a dotted capability_id",
                    detail={
                        "field": "selection.capability_id",
                        "reason": "invalid capability id",
                        "expected": "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$",
                        "actual": self.capability_id,
                    },
                )
            if self.task_kind is not None:
                raise ScientificPreconditionError(
                    "a capability selection must not name a task_kind",
                    detail={
                        "field": "selection.task_kind",
                        "reason": "both selection modes were supplied",
                        "expected": None,
                        "actual": self.task_kind,
                    },
                )
        else:
            if self.capability_id is not None:
                raise ScientificPreconditionError(
                    "an existing-task-kind selection must not name a capability_id",
                    detail={
                        "field": "selection.capability_id",
                        "reason": "both selection modes were supplied",
                        "expected": None,
                        "actual": self.capability_id,
                    },
                )
            if not isinstance(self.task_kind, str) or self.task_kind not in TASK_KINDS:
                raise UnknownTaskKindError(str(self.task_kind))

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped selection object."""
        return {
            "mode": self.mode.value,
            "capability_id": self.capability_id,
            "task_kind": self.task_kind,
        }


@dataclass(frozen=True)
class PreflightCheck:
    """One scientific precondition outcome (``None`` means not verifiable)."""

    id: str
    satisfied: bool | None
    reason: str
    expected: object = None
    actual: object = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe check view."""
        return {
            "id": self.id,
            "satisfied": self.satisfied,
            "reason": self.reason,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class PreflightResult:
    """Side-effect-free preflight outcome for one binding."""

    outcome: PreflightOutcome | str
    checks: tuple[PreflightCheck, ...] = ()

    def __post_init__(self) -> None:
        try:
            outcome = PreflightOutcome(self.outcome)
        except ValueError as exc:
            raise ScientificPreconditionError(
                f"unknown preflight outcome {self.outcome!r}"
            ) from exc
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "checks", tuple(self.checks))
        if outcome is PreflightOutcome.READY and any(
            check.satisfied is False for check in self.checks
        ):
            raise ScientificPreconditionError(
                "a ready preflight cannot contain a failed scientific check"
            )

    @property
    def ready(self) -> bool:
        """Return whether the binding is ready to execute."""
        return self.outcome is PreflightOutcome.READY

    def failures(self) -> tuple[PreflightCheck, ...]:
        """Return the failed checks."""
        return tuple(check for check in self.checks if check.satisfied is False)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe preflight view."""
        return {
            "outcome": self.outcome.value,
            "checks": [check.to_dict() for check in self.checks],
        }


# ---------------------------------------------------------------------------
# Input conversion — the authoritative TaskRequest contract
# ---------------------------------------------------------------------------


def _coerce_task_kind(value: TaskKindReference | TaskKind | str) -> str:
    """Return a validated task-kind id or raise the typed error."""
    if isinstance(value, TaskKindReference):
        return value.kind
    if isinstance(value, TaskKind):
        return value.value
    return TaskKindReference(value).kind


def build_task_request(
    task_kind: TaskKindReference | TaskKind | str,
    inputs: TaskRequest | Mapping[str, object] | None,
) -> TaskRequest | None:
    """Convert capability inputs into the existing ``TaskRequest``.

    The conversion always goes through :meth:`TaskRequest.from_dict`, so
    schema version 1, the fifteen option types and the existing payload
    contract remain authoritative.  A missing ``task`` field is filled with
    the resolved task kind; a present but different one is a scientific
    error rather than a silent retarget.

    Args:
        task_kind: Resolved existing task kind.
        inputs: A ready ``TaskRequest``, its JSON mapping form, or ``None``.

    Returns:
        The parsed request, or ``None`` when no inputs were supplied.

    Raises:
        UnknownTaskKindError: The task-kind reference is not in the inventory.
        ScientificPreconditionError: The request targets another task.
        TaskInputError: The mapping violates the request contract.
    """
    kind = _coerce_task_kind(task_kind)
    if inputs is None:
        return None
    if isinstance(inputs, TaskRequest):
        if inputs.task.value != kind:
            raise ScientificPreconditionError(
                f"request targets task {inputs.task.value!r}, not {kind!r}",
                detail={
                    "field": "request.task",
                    "reason": "request.task disagrees with the resolved task kind",
                    "expected": kind,
                    "actual": inputs.task.value,
                },
            )
        return inputs
    if not isinstance(inputs, Mapping):
        raise ScientificPreconditionError(
            "capability inputs must be a TaskRequest or its JSON mapping",
            detail={
                "field": "request",
                "reason": "unsupported input type",
                "expected": "TaskRequest | mapping",
                "actual": type(inputs).__name__,
            },
        )
    payload = dict(inputs)
    declared_task = payload.get("task", kind)
    if declared_task != kind:
        raise ScientificPreconditionError(
            f"request targets task {declared_task!r}, not {kind!r}",
            detail={
                "field": "request.task",
                "reason": "request.task disagrees with the resolved task kind",
                "expected": kind,
                "actual": declared_task,
            },
        )
    payload["task"] = kind
    return TaskRequest.from_dict(payload)


# ---------------------------------------------------------------------------
# Scientific-only preflight
# ---------------------------------------------------------------------------


def _electron_count(symbols: tuple[str, ...], charge: int) -> int | None:
    """Return the electron count for inline symbols, or ``None``."""
    total = 0
    for symbol in symbols:
        z_value = ATOMIC_NUMBER.get(symbol.strip().capitalize())
        if z_value is None:
            return None
        total += z_value
    return total - charge


def _read_attribute(value: object, name: str) -> object:
    """Read *name* from a mapping or an object."""
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _analysis_checks(
    capability_id: str,
    analysis_input: object | None,
) -> tuple[PreflightCheck, ...]:
    """Validate a pure-analysis payload without reading or writing artifacts."""
    preflight = getattr(analysis_input, "preflight_checks", None)
    if callable(preflight):
        return tuple(
            PreflightCheck(
                id=str(row["id"]),
                satisfied=row["satisfied"],  # type: ignore[arg-type]
                reason=str(row["reason"]),
                expected=row.get("expected"),
                actual=row.get("actual"),
            )
            for row in preflight()
        )
    if capability_id != "vibration.analyze":
        return (
            PreflightCheck(
                id="analysis_input_supported",
                satisfied=False,
                reason=f"capability {capability_id!r} has no declared analysis preflight",
                expected="an analysis payload with preflight_checks()",
                actual=type(analysis_input).__name__,
            ),
        )
    if analysis_input is None:
        return (
            PreflightCheck(
                id="frequency_products_available",
                satisfied=False,
                reason="pure analysis requires existing frequency products",
                expected="frequency products",
                actual=None,
            ),
        )
    frequencies = _read_attribute(analysis_input, "frequencies")
    vectors = _read_attribute(analysis_input, "mode_vectors")
    has_products = bool(frequencies) or bool(vectors)
    return (
        PreflightCheck(
            id="frequency_products_available",
            satisfied=has_products,
            reason=(
                "existing frequency products supplied"
                if has_products
                else "frequency products are empty; analysis never recomputes them"
            ),
            expected="non-empty frequencies or mode_vectors",
            actual={
                "frequencies": len(frequencies) if isinstance(frequencies, (list, tuple)) else 0,
                "mode_vectors": len(vectors) if isinstance(vectors, Mapping) else 0,
            },
        ),
    )


def _scientific_checks(request: TaskRequest) -> tuple[PreflightCheck, ...]:
    """Return the side-effect-free scientific precondition checks."""
    checks: list[PreflightCheck] = []
    structure = request.structure
    has_inline = (
        structure is not None
        and structure.coordinates is not None
        and structure.symbols is not None
    )
    has_path = structure is not None and structure.path is not None
    supplied = structure is not None and (has_inline or has_path)
    checks.append(
        PreflightCheck(
            id="structure_supplied",
            satisfied=supplied,
            reason=(
                "a resolvable structure reference was supplied"
                if supplied
                else "no structure path or inline geometry was supplied"
            ),
            expected="path or inline coordinates+symbols",
            actual=None if structure is None else structure.to_dict(),
        )
    )
    if not supplied or structure is None:
        return tuple(checks)

    if has_inline:
        coordinates = structure.coordinates or ()
        symbols = structure.symbols or ()
        geometry_ok = len(coordinates) == len(symbols) and len(symbols) > 0
        geometry_ok = geometry_ok and all(
            len(row) == 3 and all(math.isfinite(float(component)) for component in row)
            for row in coordinates
        )
        checks.append(
            PreflightCheck(
                id="structure_geometry_finite_angstrom",
                satisfied=geometry_ok,
                reason=(
                    "inline geometry has one finite 3-vector per atom in angstrom"
                    if geometry_ok
                    else "inline geometry is empty, ragged or non-finite"
                ),
                expected="one finite 3-vector (angstrom) per atom",
                actual={"n_atoms": len(symbols), "n_coordinates": len(coordinates)},
            )
        )
        n_electrons = _electron_count(symbols, request.charge)
        parity_ok = (
            None
            if n_electrons is None
            else n_electrons > 0 and electron_parity_ok(n_electrons, request.multiplicity)
        )
        checks.append(
            PreflightCheck(
                id="charge_multiplicity_consistent",
                satisfied=parity_ok,
                reason=(
                    "charge and multiplicity are consistent with the electron count"
                    if parity_ok
                    else "charge/multiplicity are inconsistent or the electron count is unknown"
                ),
                expected="positive electron count with compatible multiplicity parity",
                actual={
                    "charge": request.charge,
                    "multiplicity": request.multiplicity,
                    "n_electrons": n_electrons,
                },
            )
        )
    else:
        checks.append(
            PreflightCheck(
                id="charge_multiplicity_consistent",
                satisfied=None,
                reason=(
                    "path-only input: the electron count is resolved at execution, "
                    "never by reading or writing an artifact during preflight"
                ),
                expected="inline geometry for a binding-time electron count",
                actual=None,
            )
        )

    level = request.level
    method = level.method.strip()
    basis = level.basis.strip()
    checks.append(
        PreflightCheck(
            id="method_or_basis",
            satisfied=not (level.method and not method) and not (level.basis and not basis),
            reason=(
                "method/basis are either explicit or left for the translation defaults"
                if not (level.method and not method) and not (level.basis and not basis)
                else "method/basis contain blank values"
            ),
            expected="non-blank method/basis or empty fields for translation defaults",
            actual={"method": level.method, "basis": level.basis},
        )
    )

    state = request.electronic_state
    if state is not None:
        target = int(state.target_multiplicity)
        state_ok = target >= 1
        if has_inline:
            n_electrons = _electron_count(structure.symbols or (), request.charge)
            if n_electrons is not None:
                state_ok = state_ok and n_electrons > 0 and electron_parity_ok(n_electrons, target)
        checks.append(
            PreflightCheck(
                id="electronic_state_applicable",
                satisfied=state_ok,
                reason=(
                    "electronic state multiplicity is compatible with the electron count"
                    if state_ok
                    else "electronic state multiplicity is not compatible with the electron count"
                ),
                expected="positive target multiplicity with compatible parity",
                actual={"state_id": state.state_id, "target_multiplicity": target},
            )
        )

    if request.task is TaskKind.OPTIMIZE and isinstance(request.options, OptimizeOptions):
        ts_enabled = request.options.ts is not None and request.options.ts.enabled
        ts_requested = request.options.mode is OptimizationMode.TRANSITION_STATE or ts_enabled
        expected_role = StructureRole.TRANSITION_STATE if ts_requested else StructureRole.MINIMUM
        role_ok = structure.role is expected_role
        checks.append(
            PreflightCheck(
                id="optimization_mode_applicable",
                satisfied=role_ok,
                reason=(
                    "optimization mode and structure role agree"
                    if role_ok
                    else "optimization mode and structure role disagree"
                ),
                expected=expected_role.value,
                actual=structure.role.value,
            )
        )
    return tuple(checks)


# ---------------------------------------------------------------------------
# The binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskBinding:
    """One capability-or-task reference bound to one existing implementation."""

    selection: BindingSelection
    request: TaskRequest | None = field(default=None, repr=False)
    descriptor: CapabilityDescriptor | None = field(default=None, repr=False, compare=False)
    analysis_input: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.selection, BindingSelection):
            raise ScientificPreconditionError("selection must be a BindingSelection")
        if self.descriptor is not None and not isinstance(self.descriptor, CapabilityDescriptor):
            raise ScientificPreconditionError("descriptor must be a CapabilityDescriptor")
        task_kind = self.task_kind
        if self.request is not None and task_kind is not None:
            if self.request.task.value != task_kind:
                raise ScientificPreconditionError(
                    f"bound request targets {self.request.task.value!r}, not {task_kind!r}",
                    detail={
                        "field": "request.task",
                        "reason": "bound request disagrees with the resolved task kind",
                        "expected": task_kind,
                        "actual": self.request.task.value,
                    },
                )

    # -- identity ------------------------------------------------------------

    @property
    def capability_id(self) -> str | None:
        """Return the selected capability id, if any."""
        if self.descriptor is not None:
            return self.descriptor.capability_id
        return self.selection.capability_id

    @property
    def implementation_id(self) -> str:
        """Return the selected implementation id."""
        if self.descriptor is not None:
            return self.descriptor.implementation_id
        return f"cccp.calculation.tasks.{self.selection.task_kind}"

    @property
    def is_analysis(self) -> bool:
        """Return whether this binding is the pure-analysis capability."""
        return self.descriptor is not None and self.descriptor.kind is CapabilityKind.PURE_ANALYSIS

    @property
    def task_kind(self) -> str | None:
        """Return the resolved existing task kind (``None`` for analysis)."""
        if self.is_analysis:
            return None
        if self.descriptor is not None:
            return self.descriptor.delegates_to_task_kind
        return self.selection.task_kind

    @property
    def dispatchable(self) -> bool:
        """Return whether this binding has an implemented single delegation."""
        if self.is_analysis:
            return True
        return self.task_kind in TASK_ENTRYPOINTS

    @property
    def delegation_target(self) -> str | None:
        """Return the one delegation target, or ``None`` when unsupported."""
        if self.is_analysis:
            return _ANALYSIS_DELEGATION_TARGETS.get(self.capability_id or "")
        entrypoint = TASK_ENTRYPOINTS.get(self.task_kind or "")
        return None if entrypoint is None else entrypoint.target

    @property
    def binding_id(self) -> str:
        """Return the deterministic dotted binding id."""
        if self.capability_id is not None:
            return f"science.{self.capability_id}"
        return f"science.task.{self.task_kind}"

    # -- preflight -----------------------------------------------------------

    def preflight(self) -> PreflightResult:
        """Return a side-effect-free scientific preflight result.

        No artifact directory, subprocess, job/task file or plugin load
        happens here.  Importing the bound task module is deferred to
        :meth:`execute`.
        """
        if self.is_analysis:
            analysis_checks = _analysis_checks(self.capability_id or "", self.analysis_input)
            return PreflightResult(
                PreflightOutcome.READY
                if all(check.satisfied is not False for check in analysis_checks)
                else PreflightOutcome.NOT_READY,
                analysis_checks,
            )
        if not self.dispatchable:
            return PreflightResult(PreflightOutcome.UNSUPPORTED_CAPABILITY)
        if self.request is None:
            return PreflightResult(
                PreflightOutcome.NOT_READY,
                (
                    PreflightCheck(
                        id="request_present",
                        satisfied=False,
                        reason="no TaskRequest was supplied for this task binding",
                        expected="TaskRequest",
                        actual=None,
                    ),
                ),
            )
        checks = _scientific_checks(self.request)
        outcome = (
            PreflightOutcome.NOT_READY
            if any(check.satisfied is False for check in checks)
            else PreflightOutcome.READY
        )
        return PreflightResult(outcome, checks)

    # -- execution -----------------------------------------------------------

    def execute(self, *, context: object | None = None) -> Any:
        """Run exactly one existing implementation for this binding.

        Compute bindings invoke one ``run_*`` task core; the analysis binding
        invokes the existing pure-analysis entry point.  Nothing else is
        called, and an unsupported binding raises instead of degrading.

        Args:
            context: Optional runtime ``TaskContext`` for a compute task.

        Returns:
            The delegated implementation's return value.

        Raises:
            UnsupportedBindingError: The binding has no implemented delegation.
            ScientificPreconditionError: A scientific precondition failed.
        """
        preflight = self.preflight()
        if preflight.outcome is PreflightOutcome.UNSUPPORTED_CAPABILITY:
            raise UnsupportedBindingError(
                f"capability {self.capability_id or self.task_kind!r} has no "
                "implemented single task delegation",
                capability_id=self.capability_id,
                implementation_id=self.implementation_id,
            )
        if not preflight.ready:
            failed = preflight.failures()
            if not failed:
                raise ScientificPreconditionError(
                    "binding preflight is not ready",
                    detail={
                        "field": "preflight",
                        "reason": "preflight is not ready",
                        "expected": "ready",
                        "actual": preflight.outcome.value,
                    },
                )
            raise ScientificPreconditionError.from_check(failed[0])

        if self.is_analysis:
            target = _ANALYSIS_DELEGATION_TARGETS.get(self.capability_id or "")
            if target is None:
                raise UnsupportedBindingError(
                    f"analysis capability {self.capability_id!r} has no entry point",
                    capability_id=self.capability_id,
                    implementation_id=self.implementation_id,
                )
            module_name, _, function_name = target.rpartition(".")
            module = importlib.import_module(module_name)
            entrypoint = getattr(module, function_name, None)
            if not callable(entrypoint):
                raise UnsupportedBindingError(
                    f"analysis entry point {target} is missing or not callable",
                    capability_id=self.capability_id,
                    implementation_id=self.implementation_id,
                )
            return entrypoint(self.analysis_input)

        entrypoint_def = TASK_ENTRYPOINTS[self.task_kind or ""]
        module = importlib.import_module(entrypoint_def.module)
        runner = getattr(module, entrypoint_def.function, None)
        if not callable(runner):
            raise UnsupportedBindingError(
                f"{entrypoint_def.module}.{entrypoint_def.function} is missing",
                capability_id=self.capability_id,
                implementation_id=self.implementation_id,
            )
        return runner(self.request, context=context)

    # -- schema-shaped view --------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped binding document.

        The compute shape follows ``task-binding.schema.json``.  The analysis
        capability has no task kind by design, so its ``binding_output``
        records ``resolved_task_kind=None`` and the analysis delegation target
        instead of inventing a task.
        """
        preflight = self.preflight()
        request_payload = None if self.request is None else self.request.to_dict()
        metadata: dict[str, object] = {
            "selection_mode": self.selection.mode.value,
            "capability_id": self.capability_id,
            "implementation_id": self.implementation_id,
            "resolved_task_kind": self.task_kind,
            "delegation_target": self.delegation_target,
            "request_contract_ref": REQUEST_CONTRACT_REF,
            "request_schema_version": TASK_REQUEST_SCHEMA_VERSION,
            "result_contract_ref": RESULT_CONTRACT_REF,
            "result_schema_version": TASK_REQUEST_SCHEMA_VERSION,
            "context_serialized": False,
        }
        return {
            "schema": TASK_BINDING_SCHEMA,
            "schema_version": TASK_BINDING_SCHEMA_VERSION,
            "binding_id": self.binding_id,
            "selection": self.selection.to_dict(),
            "request_contract": {
                "module": "cccp.calculation.requests",
                "type": "TaskRequest",
                "schema_version": TASK_REQUEST_SCHEMA_VERSION,
                "serializer": "TaskRequest.to_dict",
                "parser": "TaskRequest.from_dict",
                "unknown_field_read_rule": "S2_ignore_unknown_fields_on_read",
                "execution_unknown_field_rule": (
                    "reject_unsupported_execution_fields_at_binding_preflight"
                ),
                "schema_version_rule": "S1_reject_any_version_other_than_1",
                "authoritative": True,
            },
            "result_contract": {
                "module": "cccp.calculation.results",
                "type": "TaskResult",
                "schema_version": TASK_REQUEST_SCHEMA_VERSION,
                "serializer": "TaskResult.to_dict",
                "parser": "TaskResult.from_dict",
                "unknown_field_read_rule": "S2_ignore_unknown_fields_on_read",
                "schema_version_rule": "S1_reject_any_version_other_than_1",
                "authoritative": True,
            },
            "context_contract": {
                "module": "cccp.calculation.context",
                "type": "TaskContext",
                "serialized": False,
                "must_not_appear_as_field": True,
                "serialization_rule": "runtime_only_never_json",
            },
            "binding_output": {
                "binding_metadata": metadata,
                "request": request_payload,
                "request_json_validated_by": _REQUEST_VALIDATED_BY,
            },
            "preflight": {
                "side_effect_free": True,
                "starts_subprocess": False,
                "writes_artifacts": False,
                "loads_plugins": False,
                "network": False,
                "validates_json_structure": True,
                "validates_scientific_preconditions": True,
                "validates_platform_constraints": False,
                "platform_constraint_owner": "runtime",
                "outcomes": [outcome.value for outcome in PreflightOutcome],
                "result": preflight.to_dict(),
            },
            "delegation": {
                "single": True,
                "max_invocations_per_binding": 1,
                "entrypoint": self.delegation_target or "<unsupported>",
                "entrypoint_module": (
                    (self.delegation_target or "").rsplit(".", 1)[0]
                    if self.is_analysis
                    else "cccp.calculation.tasks"
                ),
                "no_second_executor": True,
                "no_batch_or_chain": True,
            },
            "unsupported_capability_error": {
                "code": "unknown_implementation",
                "layer": "scientific",
                "retryable": False,
                "detail_required": True,
                "error_schema_ref": _ERROR_SCHEMA_REF,
                "covers": [
                    "capability_not_in_registry",
                    "capability_implementation_not_registered",
                ],
                "maps_from_error_kind": "unsupported_capability",
                "message_template": "capability has no implemented single delegation",
            },
            "scientific_precondition_error": {
                "code": "invalid_scientific_input",
                "layer": "scientific",
                "retryable": False,
                "detail_required": True,
                "error_schema_ref": _ERROR_SCHEMA_REF,
                "covers": [
                    "charge_or_multiplicity",
                    "structure_role",
                    "method_or_basis",
                    "electronic_state",
                    "scientific_applicability",
                ],
                "maps_from_error_kind": "invalid_input",
                "message_template": "scientific precondition failed",
            },
            "validation_layers": {
                "json_structure": {
                    "owner": "contract",
                    "checks": ["TaskRequest.from_dict schema version 1"],
                    "does_not_cover": ["scientific semantics", "runtime constraints"],
                },
                "cccp_scientific_semantics": {
                    "owner": "cccp",
                    "checks": [check.id for check in preflight.checks],
                    "does_not_cover": ["platform permission", "queue", "account"],
                },
                "runtime_platform_constraints": {
                    "owner": "runtime",
                    "checks": [],
                    "does_not_cover": ["scientific input validity"],
                },
            },
            "unknown_field_policy": {
                "new_execution_request": ("reject_unsupported_execution_fields_before_delegation"),
                "historical_read": (
                    "preserve_unknown_fields_without_execution_or_recovery_eligibility"
                ),
                "unknown_major_version": "never_execute",
                "task_context": "never_serialized_never_a_field",
            },
            "compatibility": {
                "contract_version": TASK_BINDING_CONTRACT_VERSION,
                "declared_major": TASK_BINDING_SCHEMA_VERSION,
                "min_reader_schema_version": TASK_BINDING_SCHEMA_VERSION,
                "max_reader_schema_version": TASK_BINDING_SCHEMA_VERSION,
                "unknown_major_version": "reject_and_never_execute",
                "unknown_major_error_code": "unknown_major_version",
                "existing_contract_versions": {
                    "task_request": TASK_REQUEST_SCHEMA_VERSION,
                    "task_result": TASK_REQUEST_SCHEMA_VERSION,
                },
            },
        }


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def bind_capability(
    capability_id: str,
    inputs: TaskRequest | Mapping[str, object] | None = None,
    *,
    analysis_input: object | None = None,
    registry: CapabilityRegistry | None = None,
) -> TaskBinding:
    """Bind a registered capability to its one existing implementation.

    Args:
        capability_id: A registered capability id (the four built-ins by
            default).
        inputs: Capability inputs as a ``TaskRequest`` or JSON mapping for a
            compute capability.
        analysis_input: Existing frequency products for ``vibration.analyze``.
        registry: Optional registry; defaults to the built-in catalog.

    Returns:
        A validated binding.  Scientific preconditions are reported by
        :meth:`TaskBinding.preflight`; this function performs no execution.

    Raises:
        UnknownCapabilityError: The capability id is not registered.
        UnsupportedBindingError: The registered capability has no implemented
            single-task delegation.
    """
    active_registry = registry if registry is not None else builtin_registry()
    record = active_registry.require(capability_id)
    descriptor = record.descriptor
    selection = BindingSelection(
        mode=BindingSelectionMode.CAPABILITY,
        capability_id=descriptor.capability_id,
    )
    if descriptor.kind is CapabilityKind.PURE_ANALYSIS:
        # Accept the analysis payload positionally as well as by keyword; it
        # is never a TaskRequest because analysis is not a task.
        analysis_payload = analysis_input if analysis_input is not None else inputs
        return TaskBinding(
            selection=selection,
            descriptor=descriptor,
            analysis_input=analysis_payload,
        )
    if descriptor.kind is not CapabilityKind.COMPUTE:
        raise UnsupportedBindingError(
            f"capability {capability_id!r} has unknown execution semantics",
            capability_id=descriptor.capability_id,
            implementation_id=descriptor.implementation_id,
        )
    task_kind = descriptor.delegates_to_task_kind
    if task_kind not in TASK_ENTRYPOINTS:
        raise UnsupportedBindingError(
            f"capability {capability_id!r} delegates to {task_kind!r}, which has "
            "no implemented binding entry point",
            capability_id=descriptor.capability_id,
            implementation_id=descriptor.implementation_id,
        )
    request = build_task_request(task_kind, inputs)  # type: ignore[arg-type]
    return TaskBinding(selection=selection, request=request, descriptor=descriptor)


def bind_task_kind(
    task_kind: TaskKindReference | TaskKind | str,
    inputs: TaskRequest | Mapping[str, object] | None = None,
) -> TaskBinding:
    """Bind a direct reference to an existing task kind.

    All fifteen task kinds are bindable so a recipe can name an existing
    task without inventing a capability.  Only the three seeded compute kinds
    are executable through this binding; the other twelve report
    ``unsupported_capability`` at preflight and raise on execution.

    Args:
        task_kind: One of the fifteen existing task-kind ids.
        inputs: A ``TaskRequest`` or JSON mapping, or ``None`` for a pure
            reference.

    Returns:
        The typed binding.

    Raises:
        UnknownTaskKindError: The reference is not one of the fifteen kinds.
        ScientificPreconditionError: The request targets another task.
        TaskInputError: The mapping violates the request contract.
    """
    kind = _coerce_task_kind(task_kind)
    selection = BindingSelection(
        mode=BindingSelectionMode.EXISTING_TASK_KIND,
        task_kind=kind,
    )
    request = build_task_request(kind, inputs)
    return TaskBinding(selection=selection, request=request)


def preflight_binding(binding: TaskBinding) -> PreflightResult:
    """Return the side-effect-free preflight result for *binding*."""
    return binding.preflight()


def execute_binding(binding: TaskBinding, *, context: object | None = None) -> Any:
    """Execute *binding* through exactly one existing implementation."""
    return binding.execute(context=context)
