"""Read-only submission analysis for preflight/submit/edit (round-5 PR-03).

This module combines the *existing* normalization and validation owners into
one read-only result — normalized coordinates, effective methods, the scope that
was actually validated, and every parameter disposition — so preflight, submit
and edit-recalculate can never disagree on what a request means.  It performs
no I/O, never touches sources, snapshots, approvals or the store; source
verification stays in the API layer's authorized read-only checks.

Judged here:

* PES coordinate normalization (PR-01) and the effective protocol/execution mode
* the scan coordinate/point aliases (PR-01)
* IRC step/initial_hessian rejection and the maxpoints mirrors
* tsmode source references
* BatchOptimize / singlepoint effective level projection (round-7 #6):
  per-role method+basis and per-role single-point level (round-7 #2), the
  optimization level as rendered, and route/basis conflicts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp.calculations.pes.normalization import (
    CoordinateError,
    normalize_pes_coordinates,
    normalize_scan_entries,
    normalize_scan_points,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ParameterEffect",
    "SubmissionAnalysis",
    "analyze_batch_submission",
    "analyze_irc_submission",
    "analyze_pes_submission",
    "analyze_scan_submission",
    "analyze_singlepoint_submission",
    "analyze_tsmode_submission",
    "analysis_for",
]

_SUBMISSION_CONTRACT_VERSION = "acp2.submission.v1"

#: Scan driver fields that only the pointwise lane can honour (round-5 §7.4).
_POINTWISE_ONLY_DRIVER_FIELDS = (
    "retry_count",
    "retry_strategy",
    "failure_policy",
    "reuse_previous_geometry",
)

_PES_UNIMPLEMENTED_STRATEGIES = frozenset({"reverse_peb", "direct_ts"})
_PES_LEGACY_STRATEGIES = frozenset({"guided_scan"})


@dataclass(frozen=True)
class ParameterEffect:
    """One requested field and what the platform actually does with it."""

    field: str
    disposition: str  # consumed|normalized_alias|metadata_only|rejected|legacy_noop|not_applicable
    detail: str = ""


@dataclass(frozen=True)
class SubmissionAnalysis:
    """Read-only analysis result shared by preflight/submit/edit."""

    workflow: str
    validated_scope: tuple[str, ...] = ()
    unverified_checks: tuple[str, ...] = ()
    effects: tuple[ParameterEffect, ...] = ()
    normalized_input: dict[str, Any] = field(default_factory=dict)
    normalized_method: dict[str, Any] = field(default_factory=dict)
    effective: dict[str, Any] = field(default_factory=dict)
    contract_version: str = _SUBMISSION_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "validated_scope": list(self.validated_scope),
            "unverified_checks": list(self.unverified_checks),
            "parameter_effects": [
                {
                    "field": effect.field,
                    "disposition": effect.disposition,
                    **({"detail": effect.detail} if effect.detail else {}),
                }
                for effect in self.effects
            ],
            "effective": dict(self.effective),
            "contract_version": self.contract_version,
        }


def _check_scan_request_carrier(
    input_data: Mapping[str, Any], scan_request: Mapping[str, Any]
) -> None:
    """A prepared scan_request must not contradict the expanded fields."""
    carrier_coordinate = scan_request.get("coordinate")
    expanded_coordinate = input_data.get("coordinate")
    if carrier_coordinate is not None and expanded_coordinate is not None:
        carrier = normalize_pes_coordinates(
            {"coordinate": carrier_coordinate}, field_prefix="input.scan_request"
        )["coordinate"]
        expanded = normalize_pes_coordinates(
            {"coordinate": expanded_coordinate}, field_prefix="input"
        )["coordinate"]
        if carrier != expanded:
            raise CoordinateError(
                "input.scan_request.coordinate conflicts with input.coordinate",
                field="input.scan_request.coordinate",
                rule="conflicting_fields",
            )
    carrier_coordinates = scan_request.get("coordinates")
    expanded_coordinates = input_data.get("coordinates")
    if carrier_coordinates is not None and expanded_coordinates is not None:
        carrier_list = normalize_pes_coordinates(
            {"coordinates": carrier_coordinates}, field_prefix="input.scan_request"
        )["coordinates"]
        expanded_list = normalize_pes_coordinates(
            {"coordinates": expanded_coordinates}, field_prefix="input"
        )["coordinates"]
        if carrier_list != expanded_list:
            raise CoordinateError(
                "input.scan_request.coordinates conflicts with input.coordinates",
                field="input.scan_request.coordinates",
                rule="conflicting_fields",
            )
    carrier_protocol = scan_request.get("protocol")
    expanded_protocol = input_data.get("protocol")
    if (
        carrier_protocol is not None
        and expanded_protocol is not None
        and dict(carrier_protocol) != dict(expanded_protocol)
    ):
        raise CoordinateError(
            "input.scan_request.protocol conflicts with input.protocol",
            field="input.scan_request.protocol",
            rule="conflicting_fields",
        )


def _scan_execution_mode(coordinates: list[Mapping[str, Any]]) -> str:
    """Native single-coordinate scan vs per-point constrained optimizations."""
    return (
        "pointwise"
        if len(coordinates) > 1 or any(item.get("values") for item in coordinates)
        else "native_scan"
    )


def _analyze_protocol_aliases(
    protocol: Mapping[str, Any],
    *,
    effects: list[ParameterEffect],
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Normalize driver/optimizer aliases; return (driver, optimizer, mode)."""
    driver = dict(protocol.get("scan_driver") or {})
    optimizer = dict(protocol.get("scan_optimizer") or {})
    if protocol.get("execution_mode") is not None:
        raise ValueError(
            "input.protocol.execution_mode is a server output field; "
            "clients must not set it"
        )
    # driver retry_count / max_iterations are compatibility aliases of the
    # optimizer values (the executor reads the optimizer)
    for key in ("retry_count", "max_iterations"):
        if key not in driver:
            continue
        if key in optimizer and optimizer[key] != driver[key]:
            raise ValueError(
                f"protocol.scan_driver.{key} conflicts with "
                f"protocol.scan_optimizer.{key}"
            )
        optimizer[key] = driver[key]
        effects.append(
            ParameterEffect(
                f"protocol.scan_driver.{key}",
                "normalized_alias",
                f"maps to protocol.scan_optimizer.{key}",
            )
        )
    return driver, optimizer, None


def analyze_pes_submission(
    input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis:
    """Analyze a PESsearch request without touching any source."""
    input_data = dict(input_map or {})
    method_data = dict(method_map or {})
    effects: list[ParameterEffect] = []
    validated_scope: list[str] = []
    effective: dict[str, Any] = {}
    normalized_input: dict[str, Any] = {}
    normalized_method = dict(method_data)

    has_coordinate_fields = any(
        key in input_data for key in ("coordinate", "coordinates", "scan_request")
    )
    if has_coordinate_fields:
        normalized = normalize_pes_coordinates(input_data, field_prefix="input")
        normalized_input["coordinate"] = normalized["coordinate"]
        normalized_input["coordinates"] = normalized["coordinates"]
        effects.append(
            ParameterEffect(
                "input.coordinate/coordinates",
                "consumed",
                "one normalized ordered list; conflicts refused",
            )
        )
        validated_scope.append("coordinates")
        effective["coordinate"] = normalized["coordinate"]
        effective["points"] = normalized["n_points"]
        effective["execution_mode"] = _scan_execution_mode(normalized["coordinates"])

    scan_request = input_data.get("scan_request")
    if isinstance(scan_request, Mapping):
        _check_scan_request_carrier(input_data, scan_request)
        effects.append(
            ParameterEffect(
                "input.scan_request",
                "consumed",
                "carrier compared against the expanded coordinate/protocol fields",
            )
        )
        validated_scope.append("scan_request")
    elif scan_request is not None:
        raise ValueError("input.scan_request must be an object")
    protocol = dict(input_data.get("protocol") or {})
    driver, optimizer, _ = _analyze_protocol_aliases(protocol, effects=effects)
    if protocol:
        validated_scope.append("protocol")
    if driver or optimizer:
        from acp.calculations.pes.contracts import ScanOptimizer
        from acp.calculations.pes.scan import scan_optimizer_level

        warnings: list[str] = []
        level = scan_optimizer_level(ScanOptimizer.from_dict(optimizer), warnings=warnings)
        effective["optimizer_level"] = {
            "method": level.method,
            "basis": level.basis,
        }
        effective["level_warnings"] = list(warnings)
        if level.method:
            effects.append(
                ParameterEffect(
                    "protocol.scan_optimizer",
                    "consumed",
                    "canonical per-point optimization level",
                )
            )
        mode = effective.get("execution_mode")
        if mode == "native_scan":
            explicit_retry = [
                key
                for key in _POINTWISE_ONLY_DRIVER_FIELDS
                if key in driver or key in optimizer
            ]
            if explicit_retry:
                raise ValueError(
                    "native_scan does not support the per-point retry settings "
                    f"{sorted(explicit_retry)} (they only affect the pointwise lane)"
                )
            if driver:
                effects.append(
                    ParameterEffect(
                        "protocol.scan_driver",
                        "not_applicable",
                        "native scan ignores the per-point retry settings",
                    )
                )

    strategy = method_data.get("strategy")
    if strategy in _PES_UNIMPLEMENTED_STRATEGIES:
        raise ValueError(
            f"PES strategy {strategy!r} is not implemented for new executions"
        )
    if strategy in _PES_LEGACY_STRATEGIES:
        effects.append(
            ParameterEffect(
                "method.strategy=guided_scan",
                "legacy_noop",
                "the name is accepted but does not switch the algorithm",
            )
        )

    # manifest-path submissions carry a source reference instead of coordinates
    if any(key in input_data for key in ("source_job_id", "from_artifact", "from")):
        validated_scope.append("manifest_reference")

    return SubmissionAnalysis(
        workflow="PESsearch",
        validated_scope=tuple(validated_scope),
        effects=tuple(effects),
        normalized_input=normalized_input,
        normalized_method=normalized_method,
        effective=effective,
    )


def analyze_scan_submission(
    input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis:
    """Analyze a scan request (aliases and point counts) without I/O."""
    input_data = dict(input_map or {})
    method_data = dict(method_map or {})
    effects: list[ParameterEffect] = []
    validated_scope: list[str] = []
    effective: dict[str, Any] = {}
    try:
        coordinates = normalize_scan_entries(input_data, method_data)
        points = normalize_scan_points(method_data, input_data)
    except CoordinateError:
        raise
    if coordinates:
        validated_scope.append("coordinates")
        effects.append(
            ParameterEffect(
                "input/method.scan_coordinates/coordinate",
                "consumed",
                "one normalized list; disagreeing aliases refused",
            )
        )
        effective["coordinates"] = list(coordinates)
        effective["execution_mode"] = _scan_execution_mode(list(coordinates))
    if points is not None:
        validated_scope.append("points")
        effects.append(
            ParameterEffect(
                "scan_points / levels.scan.scan_coordinate_points",
                "consumed",
                "one effective point count; conflicting aliases refused",
            )
        )
        effective["points"] = points
    return SubmissionAnalysis(
        workflow="scan",
        validated_scope=tuple(validated_scope),
        effects=tuple(effects),
        normalized_input={"coordinates": list(coordinates)},
        normalized_method=dict(method_data),
        effective=effective,
    )


def analyze_irc_submission(
    input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis:
    """Analyze an IRC request: step refusal, initial_hessian, maxpoints mirrors."""
    input_data = dict(input_map or {})
    method_data = dict(method_map or {})
    effects: list[ParameterEffect] = []
    validated_scope: list[str] = []
    levels = method_data.get("levels")
    irc_level = levels.get("irc") if isinstance(levels, Mapping) else None
    if method_data.get("step") is not None or (
        isinstance(irc_level, Mapping) and irc_level.get("step") is not None
    ):
        raise ValueError(
            "IRC step is not executable (no validated ORCA IRC step mapping); omit it"
        )
    if method_data.get("initial_hessian") is not None:
        raise ValueError(
            "ORCA IRC does not support initial_hessian; the Hessian handoff is the "
            "supported path"
        )
    if method_data.get("step") is None:
        effects.append(
            ParameterEffect(
                "method.step / levels.irc.step",
                "rejected",
                "refused when explicit; omitted values are the only supported form",
            )
        )
    maxpoints_top = method_data.get("maxpoints")
    maxpoints_level = irc_level.get("maxpoints") if isinstance(irc_level, Mapping) else None
    if maxpoints_top is not None and maxpoints_level is not None and maxpoints_top != maxpoints_level:
        raise ValueError(
            "method.maxpoints conflicts with method.levels.irc.maxpoints"
        )
    maxpoints = maxpoints_top if maxpoints_top is not None else maxpoints_level
    if maxpoints is not None:
        validated_scope.append("maxpoints")
        effects.append(
            ParameterEffect("method.maxpoints / levels.irc.maxpoints", "consumed")
        )
    if input_data:
        validated_scope.append("source_reference")
    effective = {"maxpoints": maxpoints} if maxpoints is not None else {}
    return SubmissionAnalysis(
        workflow="irc",
        validated_scope=tuple(validated_scope),
        effects=tuple(effects),
        normalized_input=dict(input_data),
        normalized_method=dict(method_data),
        effective=effective,
    )


def analyze_tsmode_submission(
    input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis:
    """Analyze a tsmode request: the source bundle reference."""
    input_data = dict(input_map or {})
    effects: list[ParameterEffect] = []
    validated_scope: list[str] = ["source_reference"]
    source_job_id = input_data.get("source_job_id")
    if not isinstance(source_job_id, str) or not source_job_id:
        raise ValueError("tsmode requires source_job_id")
    entry_id = input_data.get("entry_id")
    mode_index = input_data.get("source_mode_index")
    if entry_id is not None and not isinstance(entry_id, str):
        raise ValueError("tsmode entry_id must be a string")
    if mode_index is not None and (
        isinstance(mode_index, bool) or not isinstance(mode_index, int)
    ):
        raise ValueError("tsmode source_mode_index must be an integer")
    effects.append(
        ParameterEffect("input.source_job_id", "consumed", "frequency source task")
    )
    if entry_id is not None:
        effects.append(ParameterEffect("input.entry_id", "consumed"))
    if mode_index is not None:
        effects.append(ParameterEffect("input.source_mode_index", "consumed"))
    return SubmissionAnalysis(
        workflow="tsmode",
        validated_scope=tuple(validated_scope),
        effects=tuple(effects),
        normalized_input={"source_job_id": source_job_id},
        normalized_method=dict(method_map or {}),
        effective={
            "source_job_id": source_job_id,
            **({"entry_id": entry_id} if entry_id else {}),
            **({"source_mode_index": mode_index} if mode_index is not None else {}),
        },
    )


# ── route / basis checks (round-7 #3/#6) ────────────────────────────────
#
# The single classification authority is
# :func:`cccp.qc.interfaces.route_render.detect_basis_conflicts` (built on the
# route-token classifier in :mod:`cccp.qc.method_meta`).  Its reasons are
# forwarded verbatim, with the level-relative ``field`` prefixed; nothing here
# re-implements or second-guesses the classification.  ``_minimal_route_conflicts``
# is a degraded-checkout fallback only (it logs a warning and is never reached
# while the QC classifier is importable).
#
# Cross-batch boundary (round-7): the *execution* path enforces the same
# invariant inside the ORCA renderer; this module only refuses early at
# preflight.  It never rewrites a basis, never reads a file and never runs QC.


class SubmissionAnalysisError(ValueError):
    """A statically detectable semantic error with an application envelope.

    ``params`` is a plain dict (not a method) on purpose: the preflight route
    forwards it verbatim as ``input_invalid.params`` — same shape as
    :class:`acp.api.submission_contract.SubmissionContractError`.
    """

    def __init__(self, message: str, *, params: dict[str, Any]) -> None:
        super().__init__(message)
        self.params = dict(params)


def _orbital_basis_tokens(text: object) -> list[str]:
    """Orbital-basis tokens in a method string (QC classifier, single authority)."""
    from cccp.qc.method_meta import orbital_basis_tokens

    return list(orbital_basis_tokens(str(text or "")))


def _basis_source(method: object, basis: object) -> str:
    """Where the effective orbital basis would come from (display provenance)."""
    if _orbital_basis_tokens(method):
        return "method_inline"
    if str(basis or "").strip():
        return "explicit_basis"
    return "profile_or_engine_default"


def _minimal_route_conflicts(
    method: object, basis: object, *, field_prefix: str
) -> list[dict[str, Any]]:
    """Fallback when the QC classifier is unavailable (older checkout only).

    Deliberately minimal and never a second authority in normal operation:
    logs a warning and keeps the preflight refusal instead of silently
    accepting an ambiguous basis.
    """
    method_text = str(method or "").strip()
    basis_text = str(basis or "").strip()
    inline = _orbital_basis_tokens(method_text)
    if len({token.lower() for token in inline}) > 1:
        return [
            {
                "rule": "basis_conflict",
                "field": f"{field_prefix}.method",
                "message": (
                    "the method string carries more than one orbital basis "
                    f"({inline}); ORCA keeps only the last '!' token"
                ),
                "tokens": inline,
            }
        ]
    if inline and basis_text and inline[0].strip().lower() != basis_text.lower():
        return [
            {
                "rule": "basis_conflict",
                "field": f"{field_prefix}.basis",
                "message": (
                    f"method-inline basis {inline[0]!r} conflicts with the explicit "
                    f"basis {basis_text!r}; ORCA keeps only one orbital basis"
                ),
                "inline": inline[0],
                "basis": basis_text,
            }
        ]
    return []


def _route_conflicts(
    method: object,
    basis: object,
    *,
    field_prefix: str,
    aux_j: object = None,
    aux_c: object = None,
) -> list[dict[str, Any]]:
    """Basis/aux conflicts for one level, from the single QC classifier.

    Round-7 #3/#6: the classifier is
    :func:`cccp.qc.interfaces.route_render.detect_basis_conflicts` (the
    A+C agent's pure helper, which also drives the renderer invariant).  The
    returned ``field`` is level-relative, so it is prefixed here; the rest of
    the dict (``rule``/``message``/details) is forwarded verbatim.
    """
    try:
        from cccp.qc.interfaces.route_render import detect_basis_conflicts
    except (ImportError, AttributeError):  # pragma: no cover - degraded checkout
        logger.warning(
            "round-7 basis classifier unavailable; using the minimal preflight check"
        )
        return _minimal_route_conflicts(method, basis, field_prefix=field_prefix)
    conflicts: list[dict[str, Any]] = []
    for reason in detect_basis_conflicts(
        method=str(method or "") or None,
        basis=str(basis or "") or None,
        aux_j_basis=str(aux_j) if aux_j not in (None, "") else None,
        aux_c_basis=str(aux_c) if aux_c not in (None, "") else None,
    ):
        conflict = dict(reason)
        relative = str(conflict.get("field") or "").strip()
        conflict["field"] = f"{field_prefix}.{relative}" if relative else field_prefix
        conflicts.append(conflict)
    return conflicts


def _raise_on_route_conflicts(conflicts: list[dict[str, Any]]) -> None:
    """Refuse the request when any level carries a basis conflict."""
    if not conflicts:
        return
    first = conflicts[0]
    raise SubmissionAnalysisError(
        str(first.get("message") or "conflicting basis settings"),
        params={
            "field": str(first.get("field") or "method"),
            "rule": str(first.get("rule") or "basis_conflict"),
            "conflicts": conflicts,
        },
    )


def _batch_item_roles(input_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project the role each inline batch item will execute under.

    Pure and same-authority as the loader: an explicit ``role``/``tag`` wins,
    then the XYZ ``TAG:`` comment, then the documented INT default.  No item is
    invented when the input carries no inline list.
    """
    from acp.calculations.batch._tag import normalize_tag, parse_tag_comment, role_for_tag

    items = input_data.get("items")
    if not isinstance(items, (list, tuple)) or not items:
        return []
    projected: list[dict[str, Any]] = []
    for index, entry in enumerate(items):
        if not isinstance(entry, Mapping):
            continue
        raw_declared = entry.get("role") or entry.get("tag")
        tag = normalize_tag(raw_declared) if isinstance(raw_declared, str) else None
        role_source = "declared"
        if tag is None:
            xyz = entry.get("xyz")
            if isinstance(xyz, str):
                lines = xyz.splitlines()
                tag = parse_tag_comment(lines[1] if len(lines) > 1 else "").get("tag")
                role_source = "xyz_tag" if tag else "default"
            else:
                role_source = "default"
        tag = tag or "INT"
        projected.append(
            {
                "index": index,
                "tag": tag,
                "role": "ts" if tag == "TS" else "int",
                "role_source": role_source,
                "role_value": role_for_tag(tag),
            }
        )
    return projected


def analyze_batch_submission(
    input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis:
    """Project the effective BatchOptimize levels per role (read-only).

    The single authority is :meth:`BatchMethodOptions.from_method_dict` — the
    same entry the engine consumes — so the preflight projection cannot drift
    from execution.  Per-role ``sp_method``/``sp_basis`` is part of the
    projection (round-7 #2): a role without its own single-point level shows a
    fallback to its **own** optimization level, never another role's SP level.
    """
    from acp.calculations.batch.effective_config import (
        build_effective_role,
        build_orca_summary,
    )
    from acp.calculations.batch.options import BatchMethodOptions

    method_data = dict(method_map or {})
    input_data = dict(input_map or {})
    batch_roles = method_data.get("batch_roles")
    if batch_roles is not None and not isinstance(batch_roles, Mapping):
        raise SubmissionAnalysisError(
            "method.batch_roles must be an object",
            params={"field": "method.batch_roles", "rule": "invalid_type"},
        )
    try:
        opts = BatchMethodOptions.from_method_dict(method_data)
    except (AttributeError, TypeError, ValueError) as exc:
        raise SubmissionAnalysisError(
            f"BatchOptimize method payload is not resolvable: {exc}",
            params={"field": "method", "rule": "invalid_parameter"},
        ) from exc

    effects: list[ParameterEffect] = []
    conflicts: list[dict[str, Any]] = []
    roles: dict[str, dict[str, Any]] = {}
    per_role_roles = batch_roles if isinstance(batch_roles, Mapping) else {}
    for is_ts, role_key in ((False, "int"), (True, "ts")):
        role_method, role_basis = opts.for_role(is_ts)
        sp_method, sp_basis = opts.sp_for_role(is_ts)
        effective_role = build_effective_role(opts, is_ts)
        role_cfg = per_role_roles.get(role_key)
        optimization_conflicts = _route_conflicts(
            role_method, role_basis, field_prefix=f"batch_roles.{role_key}"
        )
        single_point_conflicts = _route_conflicts(
            sp_method, sp_basis, field_prefix=f"batch_roles.{role_key}.sp"
        )
        aux_slot_conflicts: list[dict[str, Any]] = []
        if isinstance(role_cfg, Mapping):
            # Aux slots are not part of the batch level schema; a role that
            # carries one would have it ignored by the engine — flag a real
            # conflict, and record the inert value below.
            aux_slot_conflicts = _route_conflicts(
                "",
                "",
                field_prefix=f"batch_roles.{role_key}",
                aux_j=role_cfg.get("aux_j_basis"),
                aux_c=role_cfg.get("aux_c_basis"),
            )
            if not aux_slot_conflicts:
                for aux_key in ("aux_j_basis", "aux_c_basis"):
                    if role_cfg.get(aux_key) in (None, ""):
                        continue
                    effects.append(
                        ParameterEffect(
                            f"batch_roles.{role_key}.{aux_key}",
                            "not_applicable",
                            "BatchOptimize has no per-role auxiliary-basis slot; "
                            "the value is not consumed",
                        )
                    )
        level_conflicts = [
            {**conflict, "role": role_key, "level": "optimization"}
            for conflict in optimization_conflicts
        ] + [
            {**conflict, "role": role_key, "level": "role_aux"}
            for conflict in aux_slot_conflicts
        ] + [
            {**conflict, "role": role_key, "level": "single_point"}
            for conflict in single_point_conflicts
        ]
        roles[role_key] = {
            "optimization_method": role_method,
            "optimization_basis": role_basis,
            "optimization_level": effective_role.get("opt_level"),
            "single_point_method": sp_method,
            "single_point_basis": sp_basis,
            "single_point_source": opts.sp_provenance(is_ts),
            "basis_source": {
                "optimization": _basis_source(role_method, role_basis),
                "single_point": _basis_source(sp_method, sp_basis),
            },
            "orca_summary": build_orca_summary(effective_role),
            "route_conflicts": level_conflicts,
        }
        conflicts.extend(level_conflicts)
        method_source = "batch_roles" if isinstance(role_cfg, Mapping) else "legacy_flat"
        effects.append(
            ParameterEffect(
                f"role.{role_key}.optimization_level",
                "consumed",
                f"source={method_source}; the optimization method/basis executed for "
                f"{role_key.upper()} items",
            )
        )
        sp_provenance = opts.sp_provenance(is_ts)
        if sp_provenance["method"] == "role":
            sp_detail = f"the {role_key} role's own sp_method/sp_basis"
        elif sp_provenance["method"] == "single_point_method":
            sp_detail = "method.single_point_method (directly-constructed common field)"
        elif sp_provenance["method"] == "flat_single_point":
            sp_detail = "method.single_point_method/single_point_basis (legacy flat payload)"
        else:
            sp_detail = f"the {role_key} optimization level (no SP override)"
        effects.append(
            ParameterEffect(f"role.{role_key}.single_point_method", "consumed", sp_detail)
        )
    _raise_on_route_conflicts(conflicts)

    profile = method_data.get("profile") or method_data.get("profile_id")
    effective: dict[str, Any] = {
        "single_point_scope": opts.single_point_scope,
        "roles": roles,
        "items": _batch_item_roles(input_data),
        "profile": str(profile) if profile is not None else None,
    }
    return SubmissionAnalysis(
        workflow="BatchOptimize",
        validated_scope=("method", "batch_roles", "effective_levels", "route_basis"),
        effects=tuple(effects),
        normalized_method=dict(method_data),
        effective=effective,
    )


def analyze_singlepoint_submission(
    input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis:
    """Project the effective singlepoint level (read-only, method+basis+aux).

    The API normalization moves a legacy ``method.method`` into
    ``levels.single_point.functional``; the direct spelling is still accepted
    when this analyzer is called on its own.  An empty level is reported as
    unresolved (``profile_or_engine_default``) instead of inventing defaults.
    """
    method_data = dict(method_map or {})
    levels = method_data.get("levels")
    level = levels.get("single_point") if isinstance(levels, Mapping) else None
    level_mapping: Mapping[str, Any] = level if isinstance(level, Mapping) else {}
    direct = method_data.get("method")
    functional = str(level_mapping.get("functional") or direct or "").strip()
    basis = str(level_mapping.get("basis") or "").strip()
    aux_j = level_mapping.get("aux_j_basis")
    aux_c = level_mapping.get("aux_c_basis")

    conflicts = _route_conflicts(
        functional,
        basis,
        field_prefix="levels.single_point",
        aux_j=aux_j,
        aux_c=aux_c,
    )
    _raise_on_route_conflicts(conflicts)

    effective: dict[str, Any] = {
        "level_id": "single_point",
        "method": functional or None,
        "basis": basis or None,
        "basis_source": _basis_source(functional, basis),
        "dispersion": level_mapping.get("dispersion"),
        "solvent_model": level_mapping.get("solvent_model"),
        "solvent": level_mapping.get("solvent"),
        "scf_convergence": level_mapping.get("scf_convergence"),
        "route_conflicts": [],
    }
    if aux_j not in (None, ""):
        effective["aux_j_basis"] = str(aux_j)
    if aux_c not in (None, ""):
        effective["aux_c_basis"] = str(aux_c)
    unverified: list[str] = []
    if not functional:
        # Nothing in the payload pins the level; the profile/engine default is
        # only resolved at execution — never claim it here.
        unverified.append("default_level_resolution")
    effects = [
        ParameterEffect(
            "levels.single_point.functional",
            "consumed" if functional else "metadata_only",
            "executed level functional" if functional else "unset: execution default applies",
        ),
    ]
    if basis:
        effects.append(ParameterEffect("levels.single_point.basis", "consumed"))
    return SubmissionAnalysis(
        workflow="singlepoint",
        validated_scope=("single_point_level", "route_basis"),
        unverified_checks=tuple(unverified),
        effects=tuple(effects),
        normalized_method=dict(method_data),
        effective=effective,
    )


_ANALYZERS = {
    "PESsearch": analyze_pes_submission,
    "scan": analyze_scan_submission,
    "irc": analyze_irc_submission,
    "tsmode": analyze_tsmode_submission,
    "BatchOptimize": analyze_batch_submission,
    "singlepoint": analyze_singlepoint_submission,
}


def analysis_for(
    workflow: str, input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis | None:
    """Return the read-only analysis for a covered workflow (None = not covered)."""
    analyzer = _ANALYZERS.get(workflow)
    if analyzer is None:
        return None
    return analyzer(input_map, method_map)
