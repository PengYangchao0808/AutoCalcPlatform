"""Read-only submission analysis for preflight/submit/edit (round-5 PR-03).

This module combines the *existing* normalization and validation owners into
one read-only result — normalized coordinates, effective methods, the scope that
was actually validated, and every parameter disposition — so preflight, submit
and edit-recalculate can never disagree on what a request means.  It performs
no I/O, never touches sources, snapshots, approvals or the store; source
verification stays in the API layer's authorized read-only checks.

Judged here (first batch: PESsearch / scan / irc / tsmode):

* PES coordinate normalization (PR-01) and the effective protocol/execution mode
* the scan coordinate/point aliases (PR-01)
* IRC step/initial_hessian rejection and the maxpoints mirrors
* tsmode source references
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp.calculations.pes.normalization import (
    CoordinateError,
    normalize_pes_coordinates,
    normalize_scan_entries,
    normalize_scan_points,
)

__all__ = [
    "ParameterEffect",
    "SubmissionAnalysis",
    "analyze_irc_submission",
    "analyze_pes_submission",
    "analyze_scan_submission",
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


_ANALYZERS = {
    "PESsearch": analyze_pes_submission,
    "scan": analyze_scan_submission,
    "irc": analyze_irc_submission,
    "tsmode": analyze_tsmode_submission,
}


def analysis_for(
    workflow: str, input_map: Mapping[str, Any] | None, method_map: Mapping[str, Any] | None
) -> SubmissionAnalysis | None:
    """Return the read-only analysis for a covered workflow (None = not covered)."""
    analyzer = _ANALYZERS.get(workflow)
    if analyzer is None:
        return None
    return analyzer(input_map, method_map)
