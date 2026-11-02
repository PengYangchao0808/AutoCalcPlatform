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
    PROFILES,
    PROTOCOLS,
    REFINEMENT_POLICIES,
)
from acp.scheduler.jobs import (
    _BATCHOPTIMIZE_PROFILES,
    _BATCHOPTIMIZE_SCALAR_FLAGS,
    _CENSO_PRESETS,
    _CONFSEARCH_SCALAR_FLAGS,
)

__all__ = [
    "BATCH_SOURCE_TYPES",
    "IRC_TS_SOURCE_LOCKED_FIELDS",
    "SIMPLE_SOURCE_TYPES",
    "SubmissionContractError",
    "WORKFLOW_CONTRACTS",
    "validate_workflow_submission",
]

#: Source encodings the simple/scan workflows accept under ``input.source_type``
#: (the Workbench chemistry envelope).  The published contract
#: (:mod:`acp.api.workflow_contracts`) imports this set instead of restating the
#: enum, so a fourth spelling cannot appear in one surface only.
SIMPLE_SOURCE_TYPES: frozenset[str] = frozenset({"smiles", "xyz_text", "structure_asset"})

#: The only ``input.source_type`` that makes ``input.items`` executable
#: (``runner.materialize_job_input`` expands items for this spelling only).
#: There is deliberately no "items imply batch" inference at runtime: one
#: field keeps one meaning, and a submission that carries items without the
#: carrier is refused before preflight can show it as feasible.
BATCH_SOURCE_TYPES: frozenset[str] = frozenset({"batch_structures"})

#: The ten ``irc_ts_source_v1`` provenance fields the scheduler locks when a
#: replayed ``input.ts_source`` is compared with the server-derived provenance
#: (``JobManager._verified_irc_spec``).  A hand-written object that misses any
#: of them can never match the derived values, so the submit boundary refuses
#: it instead of letting preflight pass and the run fail later with a
#: misleading "source evidence changed" error.
IRC_TS_SOURCE_LOCKED_FIELDS: tuple[str, ...] = (
    "job_id",
    "product_id",
    "geometry_sha256",
    "method",
    "basis",
    "charge",
    "multiplicity",
    "imaginary_frequency_cm1",
    "frequency_evidence_sha256",
    "source_completed_at",
)


class SubmissionContractError(ValueError):
    """A submit-time semantic violation, shaped like the application envelope."""

    def __init__(
        self,
        message: str,
        *,
        field_name: str,
        rule: str = "unknown_field",
        allowed: tuple[str, ...] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.field_name = field_name
        self.rule = rule
        self.allowed = allowed
        self.details = dict(details or {})

    def params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"field": self.field_name, "rule": self.rule}
        if self.allowed is not None:
            params["allowed"] = list(self.allowed)
        params.update(self.details)
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

#: PESsearch — the direct ``mode=bond_length_scan`` branch is the first-batch
#: documented surface (plan 2026-10-30 §3).  Method carries the branch selector
#: and the manifest-path knobs the runner reads; the scan protocol itself rides
#: in ``input.scan_request``/``input.protocol`` (validated by the pydantic bond
#: scan model downstream), so ``levels`` is deliberately absent: the PESsearch
#: argv builder consumes no ``method.levels`` and a rejected key is safer than a
#: silently dropped one.
_PESSEARCH_METHOD_KEYS: frozenset[str] = frozenset(
    {
        "mode",  # "bond_length_scan" selects the direct branch
        "strategy",  # manifest-path strategy (guided_scan/reverse_peb/direct_ts)
        "select",
        "coordinate_plan",  # manifest path --plan JSON
        "scan_request",  # runner fallback carrier (input.scan_request preferred)
        # Workbench wizard metadata (submit-edit.js sends both with every
        # PESsearch submit); consumers treat them as capability metadata.
        "schema_id",
        "profile_id",
    }
)

_PESSEARCH_STRATEGIES: frozenset[str] = frozenset(
    {"guided_scan", "reverse_peb", "direct_ts"}
)

#: The three simple ORCA workflows share one chemistry envelope and one method
#: shape: ``levels`` is consumed by ``method_levels_to_cli_flags(workflow=...)``,
#: and per-level ``electronic_state`` modules are read by the scheduler's
#: ``electronic_state_from_method``.
#: Reviewed schema-id aliases: a workflow may accept more than one schema id
#: only when the alias is the *same catalog schema object* (``catalog.py``
#: registers ``pes_bond_scan`` as an alias of ``pes_scan`` for historical
#: frontend/task records).  A diverging schema must never be added here —
#: ``test_schema_aliases_are_the_same_catalog_object`` pins that.
_SCHEMA_ALIASES: dict[str, frozenset[str]] = {
    "PESsearch": frozenset({"pes_scan", "pes_bond_scan"}),
}


_SIMPLE_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "input_artifact",
        "source",
        "input",
        "smiles",
        "source_type",
        "charge",
        "multiplicity",
        "source_ref",
        "asset_id",
        # v1 API accepts a top-level xyz_text (inp.get("source") or inp.get("xyz_text")
        # in v1_routes) alongside the structured source envelope.
        "xyz_text",
    }
)
#: profile_id/profile select the schema profile whose levels the edit
#: projection expands (scheduler edit_parameters.method_projection_for_edit).
_SIMPLE_METHOD_KEYS: frozenset[str] = frozenset(
    {
        "levels",
        "schema_id",
        # edit-projection metadata (profile expansion happens on edit, not on
        # the initial submit; execution reads levels)
        "profile_id",
        "profile",
        # consumed by the frozen attempt envelope (attempt_freeze reads
        # method.method as the direct method name)
        "method",
    }
)

#: xtb_optimize has its own consumption path (``levels.xtb_opt``:
#: gfn/opt_level/max_steps/solvent/solvent_model — runner.py:1693), not the ORCA
#: simple field set.
_XTB_OPTIMIZE_METHOD_KEYS: frozenset[str] = frozenset(
    {"levels", "schema_id", "profile_id", "profile"}
)

#: IRC reads its source reference through ``_resolve_irc_source_reference``
#: (source_job_id/source_product_id/source_id/source_ref) and requires the
#: verified ``ts_source`` provenance (runner).  The public source reference, the
#: internal ``ts_source`` and the CLI ``--ts-provenance-json`` carrier are
#: distinct surfaces: ``ts_source`` is **server-derived** from the source
#: reference, so it is accepted only as a complete replay (see
#: ``_validate_irc_ts_source``), never as a hand-written partial object.
_IRC_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "source_job_id",
        "source_product_id",
        "source_id",
        "source_ref",
        "ts_source",
        "ts_provenance",
        "input",
        "input_role",
        "directions",
        "charge",
        "multiplicity",
        "source_type",
        "source",
        "smiles",
        "input_artifact",
    }
)
_IRC_METHOD_KEYS: frozenset[str] = frozenset(
    {"levels", "schema_id", "method", "functional", "basis", "maxpoints", "step"}
)

#: tsmode consumes a frequency source bundle resolved by
#: ``_prepare_tsmode_input`` (source_job_id + entry_id/source_mode_index), plus
#: optimization knobs read straight from input; the runner reads no method keys.
_TSMODE_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "source_job_id",
        "entry_id",
        "source_mode_index",
        "allow_unverified_mapping",
        "max_iterations",
        "recalc_hess",
        "trust_radius",
        "retry_limit",
        "final_frequency",
        "level_overrides",
        "bundle",
        "input",
        "source_type",
    }
)

#: scan — the simple-workflow relaxed scan.  Coordinates may sit in method or
#: input (``scan_method_flags`` reads both); ``levels`` follows the dft_scan
#: schema (level id ``scan``).
_SCAN_METHOD_KEYS: frozenset[str] = frozenset(
    {
        "scan_coordinates",
        "coordinate",
        "scan_points",
        "scan_use_scants",
        "use_scants",
        "levels",
        "schema_id",
        # The Workbench scan wizard sends the capability profile selection
        # alongside the coordinates (scheduler/capabilities.py ``pes_scan``
        # exposes a ``default`` profile); metadata-only for the runner.
        "profile_id",
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
    "dft_opt": _level_fields("confsearch_unified")["dft_opt"]
    | {"recalc_hess", "aux_j_basis", "aux_c_basis", "ri_approximation"},
    "refinement_sp": _SP_LEVEL_FIELDS,
    "screening_sp": _SP_LEVEL_FIELDS,
    # Legacy payloads still carry the CENSO level; the resolver reads
    # ``levels.censo.ewin`` and per-level solvent fields.
    "censo": frozenset({"ewin", "solvent", "solvent_model"}),
}
# ``levels`` also carries scalar knobs at its top level (engine reads them).
_LEVEL_SCALARS: frozenset[str] = frozenset({"refinement_threshold"})

#: Flat keys the Workbench sends as compatibility mirrors next to batch_roles.
#: They are inert when batch_roles is present (roles are authoritative); any
#: other flat setting is refused instead of being silently dropped.
_WORKBENCH_FLAT_MIRRORS: frozenset[str] = frozenset(
    {
        "optimization_method",
        "optimization_basis",
        "single_point_method",
        "single_point_basis",
        "temperature",
        "pressure",
        "scale_factor",
    }
)

# Role field -> flat method field (Workbench `applyBatchOptimizeMethodFields`).
_ROLE_FLAT_ALIASES: dict[str, str] = {
    "method": "optimization_method",
    "basis": "optimization_basis",
    "sp_method": "single_point_method",
    "sp_basis": "single_point_basis",
}

#: Top-level single-point mirrors and the role field each one must land in.
#: ``sp_method``/``sp_basis`` are accepted as aliases **only** when
#: ``batch_roles`` is present (see ``_validate_method``); there is no flat CLI
#: consumer for those spellings, so accepting them without a role would drop
#: the value silently.
_SP_ROLE_MIRRORS: dict[str, tuple[str, str]] = {
    "single_point_method": ("int", "sp_method"),
    "single_point_basis": ("int", "sp_basis"),
    "sp_method": ("int", "sp_method"),
    "sp_basis": ("int", "sp_basis"),
}

_WORKBENCH_MIRROR_DEFAULTS: dict[str, tuple[float, ...]] = {
    "temperature": (298.15,),
    "pressure": (1.0,),
    "scale_factor": (0.9905,),
}


def _is_workbench_default(key: str, value: object) -> bool:
    """The Workbench keeps these flat defaults next to a user-set role value."""
    defaults = _WORKBENCH_MIRROR_DEFAULTS.get(key)
    if not defaults:
        return False
    try:
        return any(float(value) == item for item in defaults)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _same_mirror_value(flat: object, role: object) -> bool:
    """Compare a mirror with its role value the way the consumer would."""
    if flat == role:
        return True
    try:
        return float(flat) == float(role)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(flat).strip().lower() == str(role).strip().lower()


def _flat_mirror_source(key: str) -> tuple[str, str]:
    """Return the (role, role field) a flat method key mirrors.

    BatchMethodOptions builds the shared settings from the INT role and only
    the ``transition_state_*`` family from TS (see
    ``acp/calculations/batch/options.py``); a flat value matching *another*
    role is not a mirror of the executed value.  The single-point level is
    per-role (round-7 #2): the flat ``single_point_*``/``sp_*`` spellings
    mirror the INT role only.
    """
    if key.startswith("transition_state_"):
        return "ts", key[len("transition_state_") :]
    if key.startswith("minimum_"):
        return "int", key[len("minimum_") :]
    if key.startswith("optimization_"):
        return "int", key[len("optimization_") :]
    if key.startswith("single_point_"):
        return "int", f"sp_{key[len('single_point_') :]}"
    if key in _SP_ROLE_MIRRORS:
        return _SP_ROLE_MIRRORS[key]
    return "int", key
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
                "schema_version",
                "source_job_id",
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
    "PESsearch": WorkflowContract(
        method_keys=_PESSEARCH_METHOD_KEYS,
        method_enums={
            "mode": frozenset({"bond_length_scan"}),
            "strategy": _PESSEARCH_STRATEGIES,
        },
        input_keys=frozenset(
            {
                # chemistry envelope (input_chemistry_flags)
                "input_artifact",
                "source",
                "input",
                "smiles",
                "source_type",
                "charge",
                "multiplicity",
                "source_ref",
                "asset_id",
                # mode=bond_length_scan branch (BondLengthScanJobInput)
                "coordinate",
                "coordinates",
                "selection",
                "protocol",
                "source_job_id",
                "from_artifact",
                # manifest-path extras the runner reads from input
                "from",
                "coordinate_plan",
                "product",
                "ts_guess",
                # prepared carrier (edit/replay); normalized and compared against
                # the expanded coordinate/protocol fields
                "scan_request",
            }
        ),
        required_input_any=(
            "source",
            "coordinate",
            "from",
            "input",
            "smiles",
            "input_artifact",
            "source_job_id",
            "from_artifact",
        ),
    ),
    # Frozen-payload PES2TS entries (local-only): the request object ships
    # verbatim from the documented upstream schema; ACP validates only that the
    # payload is present and that no undeclared top-level key sneaks in.
    "XtbPathSearch": WorkflowContract(
        method_keys=frozenset({"path_request"}),
        input_keys=frozenset(
            {
                "path_request",
                "request",
                "input",
                "source",
                "charge",
                "multiplicity",
                "input_artifact",
            }
        ),
        required_input_any=("path_request",),
    ),
    "OrcaGradient": WorkflowContract(
        method_keys=frozenset({"gradient_request"}),
        input_keys=frozenset(
            {
                "gradient_request",
                "request",
                "input",
                "source",
                "charge",
                "multiplicity",
                "input_artifact",
            }
        ),
        required_input_any=("gradient_request",),
    ),
    "XtbConstrainedOpt": WorkflowContract(
        method_keys=frozenset({"constrained_opt_request"}),
        input_keys=frozenset(
            {
                "constrained_opt_request",
                "request",
                "input",
                "source",
                "charge",
                "multiplicity",
                "input_artifact",
            }
        ),
        required_input_any=("constrained_opt_request",),
    ),
    "singlepoint": WorkflowContract(
        method_keys=_SIMPLE_METHOD_KEYS,
        input_keys=_SIMPLE_INPUT_KEYS,
        required_input_any=("input", "smiles", "source", "input_artifact", "xyz_text"),
        levels=_level_fields("dft_singlepoint"),
    ),
    "optimize": WorkflowContract(
        method_keys=_SIMPLE_METHOD_KEYS,
        input_keys=_SIMPLE_INPUT_KEYS,
        required_input_any=("input", "smiles", "source", "input_artifact", "xyz_text"),
        levels=_level_fields("dft_optimize"),
    ),
    "frequency": WorkflowContract(
        method_keys=_SIMPLE_METHOD_KEYS,
        input_keys=_SIMPLE_INPUT_KEYS,
        required_input_any=("input", "smiles", "source", "input_artifact", "xyz_text"),
        levels=_level_fields("dft_frequency"),
    ),
    "xtb_optimize": WorkflowContract(
        method_keys=_XTB_OPTIMIZE_METHOD_KEYS,
        input_keys=_SIMPLE_INPUT_KEYS,
        required_input_any=("input", "smiles", "source", "input_artifact", "xyz_text"),
        levels=_level_fields("xtb_optimize"),
    ),
    "irc": WorkflowContract(
        method_keys=_IRC_METHOD_KEYS,
        input_keys=_IRC_INPUT_KEYS,
        # a bare structure file is not an IRC input: the two real entries are
        # source_job_id+source_product_id and source_ref.source_id (see
        # _resolve_irc_source_reference); ts_source/input_role are companions,
        # never sufficient on their own
        required_input_any=("source_job_id", "source_ref", "source_id"),
        levels=_level_fields("irc"),
    ),
    "tsmode": WorkflowContract(
        method_keys=frozenset(),
        input_keys=_TSMODE_INPUT_KEYS,
        # bundle is the API-resolved materialized form, never a public entry
        required_input_any=("source_job_id",),
    ),
    "scan": WorkflowContract(
        method_keys=_SCAN_METHOD_KEYS,
        input_keys=frozenset(
            {
                # chemistry envelope
                "input_artifact",
                "source",
                "input",
                "smiles",
                "source_type",
                "charge",
                "multiplicity",
                "source_ref",
                "asset_id",
                # coordinates may also ride in input (scan_method_flags)
                "scan_coordinates",
                "coordinate",
                # the scan validator reads a top-level xyz_text for atom checks
                "xyz_text",
            }
        ),
        required_input_any=("input", "smiles", "source", "input_artifact", "xyz_text"),
        levels={
            **_level_fields("dft_scan"),
            # Consumers beyond the UI schema: the edit-recalculate path reads
            # 1-based ``scan_coordinate_atoms`` from the scan level
            # (scheduler/edit_parameters.py); declaring it keeps a legal
            # Workbench payload submittable instead of silently refused.
            "scan": _level_fields("dft_scan")["scan"] | {"scan_coordinate_atoms"},
        },
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


def _allowed_schemas(workflow: str) -> frozenset[str]:
    """Schema ids this workflow may carry (default + reviewed aliases)."""
    from acp.catalog import get_workflow_by_id

    default_schema = str(
        (get_workflow_by_id(workflow) or {}).get("method_schema_id") or ""
    )
    if not default_schema:
        return frozenset()
    return frozenset({default_schema}) | _SCHEMA_ALIASES.get(workflow, frozenset())


def _levels_for_method(
    workflow: str, method: Mapping[str, Any], contract: WorkflowContract
) -> Mapping[str, frozenset[str]]:
    """Pick the level table for ``method.levels``.

    ``method.schema_id`` must match the workflow's catalog schema or a reviewed
    same-object alias (``_allowed_schemas``); cross-schema payloads are refused
    before this point.  The schema table supplies the level ids and the
    contract adds its documented compatibility mirrors (e.g. scan's
    ``scan_coordinate_atoms``).
    """
    schema_id = method.get("schema_id")
    if isinstance(schema_id, str) and schema_id:
        from acp.catalog import METHOD_SCHEMAS

        if schema_id in METHOD_SCHEMAS:
            try:
                schema_levels = _level_fields(schema_id)
            except (KeyError, ValueError):
                return contract.levels
            # Contract mirrors (e.g. the 1-based scan_coordinate_atoms that the
            # edit path consumes) stay additive on top of the schema table.
            merged = dict(schema_levels)
            for level_id, fields in contract.levels.items():
                merged[level_id] = merged.get(level_id, frozenset()) | fields
            return merged
    return contract.levels


def _reject_irc_step(method: Mapping[str, Any]) -> None:
    """IRC step is accepted by old payloads but has no executable consumer.

    Round-5 P0: the contract advertised ``step`` and the CLI accepted it while the
    ORCA IRC interface never consumed it (an "unused kwargs" warning), so an
    agent could believe it controlled the IRC step size.  New executions refuse
    it with a typed error; historical payloads stay readable.
    """
    if method.get("step") is not None:
        raise SubmissionContractError(
            "irc method.step is not executable (no validated ORCA IRC step "
            "mapping); omit it — maxpoints controls the iteration cap",
            field_name="method.step",
            rule="unsupported_parameter",
        )
    levels = method.get("levels")
    if isinstance(levels, Mapping):
        irc_level = levels.get("irc")
        if isinstance(irc_level, Mapping) and irc_level.get("step") is not None:
            raise SubmissionContractError(
                "irc method.levels.irc.step is not executable (no validated ORCA "
                "IRC step mapping); omit it",
                field_name="method.levels.irc.step",
                rule="unsupported_parameter",
            )


def _validate_method(workflow: str, method: Mapping[str, Any], contract: WorkflowContract) -> None:
    #: ``sp_method``/``sp_basis`` are accepted ONLY as ``batch_roles``
    #: mirrors (round-7 #2); the legacy flat payload keeps the documented
    #: ``single_point_*`` spelling, which has a real CLI consumer.
    conditional = (
        frozenset({"sp_method", "sp_basis"})
        if workflow == "BatchOptimize" and isinstance(method.get("batch_roles"), Mapping)
        else frozenset()
    )
    allowed_keys = contract.method_keys | conditional
    unknown = sorted(str(key) for key in method if str(key) not in allowed_keys)
    if unknown:
        hint = ""
        if unknown[0] in _SP_ROLE_MIRRORS:
            hint = (
                " (sp_method/sp_basis are only accepted as batch_roles mirrors; "
                "the flat form is method.single_point_method/single_point_basis)"
            )
        raise SubmissionContractError(
            f"{workflow} method has an undeclared field {unknown[0]!r}{hint}",
            field_name=f"method.{unknown[0]}",
            rule="unknown_field",
            allowed=tuple(sorted(allowed_keys)),
        )
    if workflow == "irc":
        _reject_irc_step(method)
    # The schema/workflow consistency is independent of the levels container:
    # a wrong schema_id must be refused even when levels is omitted.
    schema_id = method.get("schema_id")
    if isinstance(schema_id, str) and schema_id:
        allowed_schemas = _allowed_schemas(workflow)
        if allowed_schemas and schema_id not in allowed_schemas:
            raise SubmissionContractError(
                f"{workflow} method.schema_id must be one of "
                f"{sorted(allowed_schemas)} (got {schema_id!r}); cross-schema "
                "method payloads would be accepted but ignored by the runner",
                field_name="method.schema_id",
                rule="unsupported_combination",
                allowed=tuple(sorted(allowed_schemas)),
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
        allowed_levels = _levels_for_method(workflow, method, contract)
        for level_id, level_value in levels.items():
            if level_id in _LEVEL_SCALARS:
                if isinstance(level_value, bool) or not isinstance(level_value, (int, float)):
                    raise SubmissionContractError(
                        f"{workflow} method.levels.{level_id} must be a number",
                        field_name=f"method.levels.{level_id}",
                        rule="invalid_type",
                    )
                continue
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
    if "batch_roles" in method:
        roles = _as_mapping(method["batch_roles"], "method.batch_roles")
        for role_id, role_value in roles.items():
            _as_mapping(role_value, f"method.batch_roles.{role_id}")
        # The flag builder returns early when batch_roles is present, so only
        # the documented Workbench compatibility mirrors may stay at the top
        # level; every other flat setting would be silently ignored.  Value
        # equivalence is NOT re-judged here: the engine applies fallbacks
        # (TS method -> INT method, recalc auto -> numeric) that the API layer
        # cannot reproduce without becoming a second implementation.
        unsupported = sorted(
            key
            for key in (
                *_BATCHOPTIMIZE_SCALAR_FLAGS,
                "opt_recalc_hess",
                "minimum_opt_recalc_hess",
                "transition_state_opt_recalc_hess",
                "scf_orbital_inherit",
            )
            if key in method and key not in _WORKBENCH_FLAT_MIRRORS
        )
        if unsupported:
            raise SubmissionContractError(
                f"method.{unsupported[0]} would be ignored while method.batch_roles is set; "
                "put it in batch_roles instead",
                field_name=f"method.{unsupported[0]}",
                rule="shadowed_field",
            )
        # Round-7 #2 / round-8 #4: every published Workbench mirror (per-role SP
        # aliases plus the flat optimization_*/single_point_*/thermo keys) must
        # land in the role the engine executes.  A missing role (or a role
        # without the mirrored field) would make the flat value inert, so it is
        # refused instead of dropped silently.  ``temperature``/``pressure``/
        # ``scale_factor`` only count as mirrors when they are not the Workbench
        # default (the UI keeps the default next to a user-set role value).
        # The value-mismatch branch below stays a separate rule (shadowed_field).
        mirror_candidates = _WORKBENCH_FLAT_MIRRORS | frozenset(_SP_ROLE_MIRRORS)
        for flat_key in sorted(mirror_candidates):
            if flat_key not in method:
                continue
            flat_value = method[flat_key]
            if flat_value is None or flat_value == "":
                continue
            if _is_workbench_default(flat_key, flat_value):
                continue
            role_id, role_field = _flat_mirror_source(flat_key)
            role_value = roles.get(role_id)
            role_field_value = (
                role_value.get(role_field) if isinstance(role_value, Mapping) else None
            )
            if not isinstance(role_value, Mapping) or role_field_value in (None, ""):
                has_role = isinstance(role_value, Mapping)
                target_state = (
                    f"batch_roles.{role_id} does not set {role_field}"
                    if has_role
                    else f"batch_roles has no {role_id!r} entry"
                )
                raise SubmissionContractError(
                    f"method.{flat_key} has no matching batch_roles target "
                    f"({target_state}); the flat mirror would be dropped. "
                    f"Write method.batch_roles.{role_id}.{role_field} = "
                    f"{flat_value!r} or remove method.{flat_key}",
                    field_name=f"method.{flat_key}",
                    rule="missing_role_mirror",
                    details={
                        "role": f"batch_roles.{role_id}.{role_field}",
                        "role_field": role_field,
                        "role_present": has_role,
                        "flat_value": flat_value,
                    },
                )
        # A Workbench mirror that carries a *different* value than the role the
        # engine executes is a silent swallow; the role value wins downstream.
        mirror_keys = (_WORKBENCH_FLAT_MIRRORS | frozenset(_SP_ROLE_MIRRORS)) & set(method)
        for flat_key in sorted(mirror_keys):
            role_id, role_field = _flat_mirror_source(flat_key)
            role_value = roles.get(role_id)
            if not isinstance(role_value, Mapping) or role_field not in role_value:
                continue
            if role_value[role_field] is None:
                continue
            if _is_workbench_default(flat_key, method[flat_key]):
                continue
            if not _same_mirror_value(method[flat_key], role_value[role_field]):
                raise SubmissionContractError(
                    f"method.{flat_key} conflicts with batch_roles.{role_id}.{role_field}; "
                    "the role value is the one executed",
                    field_name=f"method.{flat_key}",
                    rule="shadowed_field",
                    details={
                        "role": f"batch_roles.{role_id}.{role_field}",
                        "role_value": role_value[role_field],
                        "flat_value": method[flat_key],
                    },
                )
    if "select" in method and method["select"] is not None:
        select = method["select"]
        if not isinstance(select, str) and not isinstance(select, (list, tuple)):
            raise SubmissionContractError(
                "method.select must be a string or a list",
                field_name="method.select",
                rule="invalid_type",
            )
def _validate_input(
    workflow: str,
    payload: Mapping[str, Any],
    contract: WorkflowContract,
    *,
    require_sources: bool = True,
) -> None:
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
        if isinstance(value, dict):
            return bool(value)
        if isinstance(value, (list, tuple)):
            # An explicitly supplied list (even empty) is a declared source:
            # the legacy API defers item validation to the resolver.
            return True
        return True

    if (
        require_sources
        and contract.required_input_any
        and not any(_present(payload.get(key)) for key in contract.required_input_any)
    ):
        raise SubmissionContractError(
            f"{workflow} input needs one of {list(contract.required_input_any)}",
            field_name="input",
            rule="missing_input",
            allowed=contract.required_input_any,
        )
    # ``batch_structures`` item lists are validated by the resolver downstream:
    # the legacy API accepts an empty list at create time (create-now-fail-later),
    # so the contract must not turn that into a 422.
    #
    # Round 8 K2: ``items`` is executable only when the carrier is declared.
    # ``runner.materialize_job_input`` switches on ``input.source_type`` alone,
    # so a non-empty ``items`` list without ``source_type="batch_structures"``
    # used to pass preflight and fail at dispatch with a generic "requires a
    # batch artifact" error.  Refuse it here instead of inferring the carrier
    # at runtime (one field, one meaning); an *empty* list stays a declared
    # source (the legacy create-now-fail-later shape) and the artifact/items
    # file carriers keep working without a ``source_type``.
    #
    # Round 3: the scheduler pins ``--layout-mode single_flat`` for every
    # BatchOptimize dispatch (runner), so >1 inline item fails 30 s later
    # inside the engine.  This is statically knowable — refuse it here.
    if contract.batch_structures_only:
        items = payload.get("items")
        if isinstance(items, (list, tuple)) and len(items) > 0:
            source_type = payload.get("source_type")
            if not isinstance(source_type, str) or source_type not in BATCH_SOURCE_TYPES:
                raise SubmissionContractError(
                    'input.items requires input.source_type="batch_structures"; '
                    "the platform never infers the batch carrier from the items "
                    "list (omit input.items to use from_artifact/items_file)",
                    field_name="input.source_type",
                    rule="missing_source_type",
                    allowed=tuple(sorted(BATCH_SOURCE_TYPES)),
                    details={
                        "items_count": len(items),
                        "source_type": source_type if isinstance(source_type, str) else None,
                        "hint": (
                            'set input.source_type="batch_structures" next to '
                            "input.items, or provide input.from_artifact/"
                            "input.items_file instead of inline items"
                        ),
                    },
                )
        if isinstance(items, (list, tuple)) and len(items) > 1:
            raise SubmissionContractError(
                "BatchOptimize runs one structure per task (single_flat layout); "
                f"got {len(items)} items — submit one task per structure",
                field_name="input.items",
                rule="layout_limit",
                details={"limit": 1, "received": len(items)},
            )


def _validate_irc_ts_source(input_mapping: Mapping[str, Any]) -> None:
    """Refuse a hand-written *partial* ``input.ts_source`` (round-8 P0).

    The platform derives the complete ``irc_ts_source_v1`` provenance from
    ``source_job_id``/``source_product_id`` (``resolve_verified_ts_source``) and
    overwrites the spec with it.  The client object is only compared for source
    stability against the derived values (``JobManager._verified_irc_spec``
    locks :data:`IRC_TS_SOURCE_LOCKED_FIELDS`), so a partial object (e.g. the
    old published example ``{"schema": "irc_ts_source_v1"}``) can never match:
    it used to pass preflight and fail at submit with the misleading "IRC source
    evidence changed since the previous submission" message.

    A *complete* object stays accepted (idempotent replay / CLI compatibility);
    equality with the derived provenance is still the manager's decision.
    """
    ts_source = input_mapping.get("ts_source")
    if ts_source is None:
        return
    if not isinstance(ts_source, Mapping):
        raise SubmissionContractError(
            "input.ts_source must be an object; omit it — the platform derives "
            "the complete irc_ts_source_v1 provenance from "
            "source_job_id/source_product_id",
            field_name="input.ts_source",
            rule="invalid_type",
        )
    # A present-but-null field is not evidence: treat it as missing so the answer
    # is "partial (omit it)" rather than the misleading "changed" at the manager.
    missing = [
        field
        for field in IRC_TS_SOURCE_LOCKED_FIELDS
        if field not in ts_source or ts_source.get(field) is None
    ]
    if not missing:
        return
    raise SubmissionContractError(
        "input.ts_source is partial (missing "
        + ", ".join(missing)
        + "); omit input.ts_source — the platform derives the complete "
        "irc_ts_source_v1 provenance from source_job_id/source_product_id",
        field_name="input.ts_source",
        rule="irc_ts_source_partial",
        details={
            "missing": missing,
            "locked_fields": list(IRC_TS_SOURCE_LOCKED_FIELDS),
            "hint": (
                "remove input.ts_source; source_job_id + source_product_id are "
                "sufficient and the platform fills the provenance at submit"
            ),
        },
    )


def _validate_pes_coordinates(input_mapping: Mapping[str, Any]) -> None:
    """Shared PES coordinate normalization (round-5 P0).

    Runs the same normalization the executor uses, so a submission carrying
    both ``coordinate`` and a disagreeing ``coordinates[0]`` is refused at the
    boundary instead of silently scanning one of them.
    """
    from acp.calculations.pes.normalization import (
        CoordinateError,
        normalize_pes_coordinates,
    )

    try:
        normalize_pes_coordinates(input_mapping, field_prefix="input")
    except CoordinateError as exc:
        raise SubmissionContractError(
            str(exc),
            field_name=exc.field,
            rule=exc.rule,
            allowed=tuple(exc.params.get("allowed") or ()),
        ) from exc


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
    # An omitted input block keeps the legacy "create now, fail later" shape;
    # an explicitly empty block is still refused (P1).
    _validate_input(
        workflow, input_mapping, contract, require_sources=input is not None
    )
    if workflow == "irc":
        # Round-8 P0: ``ts_source`` is server-derived; only a complete replay is
        # accepted (the manager still compares it with the derived provenance).
        _validate_irc_ts_source(input_mapping)
    if workflow == "PESsearch" and any(
        key in input_mapping for key in ("coordinate", "coordinates", "scan_request")
    ):
        # manifest-path submissions carry no scan coordinate at all; only the
        # coordinate-bearing branch is normalized here
        _validate_pes_coordinates(input_mapping)
