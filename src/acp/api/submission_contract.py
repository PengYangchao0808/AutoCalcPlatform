"""Per-workflow semantic contract shared by ``preflight`` and ``create_job``.

Field *shape* and the top-level request policy belong to
:mod:`acp_services.entrypoints.application_params`.  What that layer cannot see
is the **meaning** of the three verbatim mappings it forwards:

* ``method`` — the scheduler translates a known subset into CLI flags; an
  unknown key used to be dropped silently, so a typo ran with defaults.
* enum values — ``refinement_policy="screen "`` or ``"screening"`` silently
  fell back instead of failing.
* input shape — an empty ``input`` was accepted and failed later (or ran a
  default molecule).

This module owns that contract for the two workflows the MCP surface documents
end to end (Confsearch, BatchOptimize).  The allowed method keys are built from
the *consumers* that produce argv (``jobs.py`` flag tables) plus an explicit
list of non-flag fields consumed elsewhere; the flag tables stay the lower
bound, never the whole contract.

Coverage is intentionally staged: top-level method keys, known enums and
container types.  Nested ``levels`` content is type-checked only — a per-level
schema is a later phase and must be reported as such by discovery.

Callers must translate :class:`SubmissionContractError` into the application
error envelope (``input_invalid`` + ``params.field/rule``); this module never
raises HTTP exceptions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Consumer tables — imported rather than copied so a new CLI flag cannot drift
# into a second allowlist.  They are a *lower bound*: non-flag fields are
# registered explicitly below (see the module docstring).
from acp.confsearch.contracts import (
    BACKENDS,
    PROTOCOLS,
    PROFILES,
    REFINEMENT_POLICIES,
)
from acp.scheduler.jobs import (
    _BATCHOPTIMIZE_PROFILES,
    _BATCHOPTIMIZE_SCALAR_FLAGS,
    _CENSO_PRESETS,
    _CONFSEARCH_SCALAR_FLAGS,
)

__all__ = [
    "SubmissionContractError",
    "WORKFLOW_CONTRACTS",
    "validate_workflow_submission",
]


class SubmissionContractError(ValueError):
    """A submit-time semantic violation, shaped like the application envelope."""

    def __init__(
        self,
        message: str,
        *,
        field_name: str,
        rule: str = "unknown_field",
        allowed: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.field_name = field_name
        self.rule = rule
        self.allowed = allowed

    def params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"field": self.field_name, "rule": self.rule}
        if self.allowed is not None:
            params["allowed"] = list(self.allowed)
        return params


@dataclass(frozen=True)
class WorkflowContract:
    """One workflow's semantic surface (the subset this phase can prove)."""

    method_keys: frozenset[str]
    method_enums: Mapping[str, frozenset[str]] = field(default_factory=dict)
    input_keys: frozenset[str] | None = None
    required_input_any: tuple[str, ...] = ()
    batch_structures_only: bool = False
    reject_batch_roles_with_scalars: bool = False


_CONFSEARCH_METHOD_KEYS: frozenset[str] = frozenset(
    {
        # flags (consumer table) plus fields consumed outside the flag helper
        *(_CONFSEARCH_SCALAR_FLAGS),
        "protocol",
        "profile",
        "profile_id",
        "refinement_policy",
        "backend",
        "preset",
        "solvent",  # censo_solvent_from_method
        "ewin",  # censo_ewin_from_method
        "levels",  # --levels JSON, read by the engine
        "schema_id",  # runner._expand_method_levels
    }
)

_BATCHOPTIMIZE_METHOD_KEYS: frozenset[str] = frozenset(
    {
        *(_BATCHOPTIMIZE_SCALAR_FLAGS),
        "profile",
        "profile_id",
        "select",
        "selected_ids",
        "batch_roles",
        "levels",
        "schema_id",
        "opt_recalc_hess",
        "minimum_opt_recalc_hess",
        "transition_state_opt_recalc_hess",
        "scf_orbital_inherit",
    }
)

WORKFLOW_CONTRACTS: dict[str, WorkflowContract] = {
    "Confsearch": WorkflowContract(
        method_keys=_CONFSEARCH_METHOD_KEYS,
        method_enums={
            "protocol": frozenset(PROTOCOLS),
            "refinement_policy": frozenset(REFINEMENT_POLICIES),
            "backend": frozenset(BACKENDS),
            "preset": frozenset(_CENSO_PRESETS),
            "profile": frozenset(PROFILES),
        },
        input_keys=frozenset({"input_artifact", "source", "input", "smiles"}),
        required_input_any=("input_artifact", "source", "input", "smiles"),
    ),
    "BatchOptimize": WorkflowContract(
        method_keys=_BATCHOPTIMIZE_METHOD_KEYS,
        method_enums={
            "profile": frozenset(_BATCHOPTIMIZE_PROFILES),
            "profile_id": frozenset(_BATCHOPTIMIZE_PROFILES),
        },
        input_keys=frozenset(
            {"source_type", "items", "from_artifact", "items_file", "input", "source"}
        ),
        # A batch_roles request returns before scalar flags are emitted, so a
        # request carrying both would silently drop the flat settings.
        reject_batch_roles_with_scalars=True,
    ),
}


def _as_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SubmissionContractError(
            f"{field_name} must be an object",
            field_name=field_name,
            rule="invalid_type",
        )
    return value


def _validate_method(workflow: str, method: Mapping[str, Any], contract: WorkflowContract) -> None:
    unknown = sorted(str(key) for key in method if str(key) not in contract.method_keys)
    if unknown:
        raise SubmissionContractError(
            f"{workflow} method has an undeclared field {unknown[0]!r}",
            field_name=f"method.{unknown[0]}",
            rule="unknown_field",
            allowed=tuple(sorted(contract.method_keys)),
        )
    for key, allowed in contract.method_enums.items():
        if key not in method or method[key] is None or method[key] == "":
            continue
        value = method[key]
        if not isinstance(value, str) or value not in allowed:
            raise SubmissionContractError(
                f"{workflow} method.{key} must be one of {sorted(allowed)}",
                field_name=f"method.{key}",
                rule="invalid_enum",
                allowed=tuple(sorted(allowed)),
            )
    for container in ("levels", "batch_roles"):
        if container in method and method[container] is not None:
            _as_mapping(method[container], f"method.{container}")
    if "select" in method and method["select"] is not None:
        select = method["select"]
        if not isinstance(select, str) and not isinstance(select, (list, tuple)):
            raise SubmissionContractError(
                "method.select must be a string or a list",
                field_name="method.select",
                rule="invalid_type",
            )
    if contract.reject_batch_roles_with_scalars and "batch_roles" in method:
        swallowed = sorted(
            key
            for key in _BATCHOPTIMIZE_SCALAR_FLAGS
            if key in method and method[key] not in (None, "")
        )
        if swallowed:
            raise SubmissionContractError(
                "method.batch_roles overrides the flat method settings; "
                "remove one of the two forms",
                field_name="method.batch_roles",
                rule="conflicting_fields",
                allowed=tuple(swallowed),
            )


def _validate_input(workflow: str, payload: Mapping[str, Any], contract: WorkflowContract) -> None:
    if contract.input_keys is not None:
        unknown = sorted(str(key) for key in payload if str(key) not in contract.input_keys)
        if unknown:
            raise SubmissionContractError(
                f"{workflow} input has an undeclared field {unknown[0]!r}",
                field_name=f"input.{unknown[0]}",
                rule="unknown_field",
                allowed=tuple(sorted(contract.input_keys)),
            )
    if contract.required_input_any and not any(
        payload.get(key) not in (None, "") for key in contract.required_input_any
    ):
        raise SubmissionContractError(
            f"{workflow} input needs one of {list(contract.required_input_any)}",
            field_name="input",
            rule="missing_input",
            allowed=contract.required_input_any,
        )
    if contract.batch_structures_only and payload.get("source_type") == "batch_structures":
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise SubmissionContractError(
                "BatchOptimize batch_structures input requires a non-empty items list",
                field_name="input.items",
                rule="missing_input",
            )


def validate_workflow_submission(
    workflow: str,
    *,
    input: Mapping[str, Any] | None,
    method: Mapping[str, Any] | None,
    resources: Mapping[str, Any] | None,
) -> None:
    """Validate the semantic contract for *workflow*.

    Workflows outside the staged coverage are accepted unchanged: this function
    must never widen its scope by guessing.

    Raises:
        SubmissionContractError: Unknown method key, illegal enum value, wrong
            container type, missing input source, or a known conflicting pair.
    """
    contract = WORKFLOW_CONTRACTS.get(workflow)
    if contract is None:
        return
    method_mapping = _as_mapping(method or {}, "method")
    input_mapping = _as_mapping(input or {}, "input")
    _ = _as_mapping(resources or {}, "resources")
    _validate_method(workflow, method_mapping, contract)
    _validate_input(workflow, input_mapping, contract)
