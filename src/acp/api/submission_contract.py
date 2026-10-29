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
from acp.catalog import METHOD_SCHEMAS
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
    # level_id -> allowed field names, derived from the catalog schema the
    # wizard and the engine both read (``METHOD_SCHEMAS``).
    levels: Mapping[str, frozenset[str]] = field(default_factory=dict)


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
        # Workbench UI selection; no scheduler consumer yet (P2 cleanup).
        "confsearch_profile",
    }
)

_BATCHOPTIMIZE_METHOD_KEYS: frozenset[str] = frozenset(
    {
        *(_BATCHOPTIMIZE_SCALAR_FLAGS),
        "profile",
        "profile_id",
        "select",
        "batch_roles",
        "levels",
        "schema_id",
        "opt_recalc_hess",
        "minimum_opt_recalc_hess",
        "transition_state_opt_recalc_hess",
        "scf_orbital_inherit",
    }
)

def _level_fields(schema_id: str) -> dict[str, frozenset[str]]:
    schema = METHOD_SCHEMAS[schema_id]
    return {
        str(level["level_id"]): frozenset(str(name) for name in level.get("fields", ()))
        for level in schema["method_levels"]
    }


_SP_LEVEL_FIELDS: frozenset[str] = frozenset(
    {
        "functional",
        "basis",
        "aux_j_basis",
        "aux_c_basis",
        "dispersion",
        "ri_approximation",
        "solvent_model",
        "solvent",
        "grid",
        "scf_convergence",
    }
)
_CONFSEARCH_LEVELS: dict[str, frozenset[str]] = {
    **_level_fields("confsearch_unified"),
    # Consumers in cccp/analysis/ensemble/levels.py accept more than the UI
    # schema lists (recalc_hess, aux_c_basis) and a screening part.
    "dft_opt": _level_fields("confsearch_unified")["dft_opt"] | {"recalc_hess"},
    "refinement_sp": _SP_LEVEL_FIELDS,
    "screening_sp": _SP_LEVEL_FIELDS,
    # Legacy payloads still carry the CENSO level; the resolver reads
    # ``levels.censo.ewin`` and per-level solvent fields.
    "censo": frozenset({"ewin", "solvent", "solvent_model"}),
}
# ``levels`` also carries scalar knobs at its top level (engine reads them).
_LEVEL_SCALARS: frozenset[str] = frozenset({"refinement_threshold"})
_BATCH_LEVELS: dict[str, frozenset[str]] = _level_fields("batch_optimize")


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
        input_keys=frozenset(
            {
                "input_artifact",
                "source",
                "input",
                "smiles",
                # Workbench chemistry envelope (consumed by input_chemistry_flags)
                "source_type",
                "charge",
                "multiplicity",
                "source_ref",
                "asset_id",
            }
        ),
        required_input_any=("input_artifact", "source", "input", "smiles"),
        levels=_CONFSEARCH_LEVELS,
    ),
    "BatchOptimize": WorkflowContract(
        method_keys=_BATCHOPTIMIZE_METHOD_KEYS,
        method_enums={
            "profile": frozenset(_BATCHOPTIMIZE_PROFILES),
            "profile_id": frozenset(_BATCHOPTIMIZE_PROFILES),
        },
        input_keys=frozenset(
            {
                "source_type",
                "items",
                "from_artifact",
                "items_file",
                "input",
                "source",
                "selected_ids",
                "charge",
                "multiplicity",
            }
        ),
        required_input_any=("items", "from_artifact", "items_file", "input", "source"),
        batch_structures_only=True,
        levels=_BATCH_LEVELS,
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
    if "levels" in method and method["levels"] is not None:
        levels = _as_mapping(method["levels"], "method.levels")
        for level_id, level_value in levels.items():
            if level_id in _LEVEL_SCALARS:
                if isinstance(level_value, bool) or not isinstance(level_value, (int, float)):
                    raise SubmissionContractError(
                        f"{workflow} method.levels.{level_id} must be a number",
                        field_name=f"method.levels.{level_id}",
                        rule="invalid_type",
                    )
                continue
            allowed_levels = contract.levels
            if level_id not in allowed_levels:
                raise SubmissionContractError(
                    f"{workflow} method.levels has an undeclared level {level_id!r}",
                    field_name=f"method.levels.{level_id}",
                    rule="unknown_field",
                    allowed=tuple(sorted(allowed_levels)),
                )
            level_mapping = _as_mapping(level_value, f"method.levels.{level_id}")
            allowed = allowed_levels[level_id] | {"engine", "_disabled", "steps"}
            unknown = sorted(str(key) for key in level_mapping if str(key) not in allowed)
            if unknown:
                raise SubmissionContractError(
                    f"{workflow} method.levels.{level_id} has an undeclared field {unknown[0]!r}",
                    field_name=f"method.levels.{level_id}.{unknown[0]}",
                    rule="unknown_field",
                    allowed=tuple(sorted(allowed)),
                )
    if "batch_roles" in method and method["batch_roles"] is not None:
        roles = _as_mapping(method["batch_roles"], "method.batch_roles")
        for role_id, role_value in roles.items():
            _as_mapping(role_value, f"method.batch_roles.{role_id}")
        batch_level = (method.get("levels") or {}).get("batch")
        if isinstance(batch_level, Mapping):
            for key in (
                *_BATCHOPTIMIZE_SCALAR_FLAGS,
                "opt_recalc_hess",
                "minimum_opt_recalc_hess",
                "transition_state_opt_recalc_hess",
                "scf_orbital_inherit",
            ):
                if key in method and key in batch_level and method[key] != batch_level[key]:
                    raise SubmissionContractError(
                        f"method.{key} disagrees with method.levels.batch.{key}",
                        field_name=f"method.{key}",
                        rule="conflicting_fields",
                    )
    if "select" in method and method["select"] is not None:
        select = method["select"]
        if not isinstance(select, str) and not isinstance(select, (list, tuple)):
            raise SubmissionContractError(
                "method.select must be a string or a list",
                field_name="method.select",
                rule="invalid_type",
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
    def _present(value: Any) -> bool:
        if value is None or value == "":
            return False
        if isinstance(value, (list, tuple, dict)):
            return bool(value)
        return True

    if contract.required_input_any and not any(
        _present(payload.get(key)) for key in contract.required_input_any
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
