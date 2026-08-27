# pyright: reportMissingImports=false
"""Quality-gate framework for mechanism studies."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from acp.calculations.irc.contracts import EndpointMatchResult, IrcResult

from ._helpers import write_json_atomic as _write_json_atomic
from .models import (
    MechanismStudy,
    PathResult,
    QualityGateResult,
)
from .providers.contracts import RefinementManifest

logger = logging.getLogger(__name__)

GATE_IDS: tuple[str, ...] = ("G0", "G1", "G2", "G3", "G4", "G5")
GateStatus = Literal["pass", "warn", "fail"]


@dataclass
class GateContext:
    """Evidence bundle passed to gate evaluators."""

    study: MechanismStudy
    path_results: dict[str, PathResult] = field(default_factory=dict)
    refinement_manifests: dict[str, RefinementManifest] = field(default_factory=dict)
    endpoint_matches: dict[str, EndpointMatchResult] = field(default_factory=dict)
    irc_results: dict[str, IrcResult] = field(default_factory=dict)
    policies: dict[str, dict[str, Any]] = field(default_factory=dict)


def _build_context(study: MechanismStudy) -> GateContext:
    metadata = study.metadata or {}
    path_results = {
        route_id: PathResult.from_dict(dict(result_data))
        for route_id, result_data in dict(metadata.get("path_results") or {}).items()
        if isinstance(result_data, dict)
    }
    refinement_manifests = {
        manifest_id: RefinementManifest.from_dict(dict(manifest_data))
        for manifest_id, manifest_data in dict(metadata.get("refinement_manifests") or {}).items()
        if isinstance(manifest_data, dict)
    }
    endpoint_matches = {
        match_id: EndpointMatchResult.from_dict(dict(match_data))
        for match_id, match_data in dict(metadata.get("endpoint_matches") or {}).items()
        if isinstance(match_data, dict)
    }
    irc_results = {
        irc_id: IrcResult.from_dict(dict(irc_data))
        for irc_id, irc_data in dict(metadata.get("irc_results") or {}).items()
        if isinstance(irc_data, dict)
    }
    policies = {
        gate_id: dict(policy_data)
        for gate_id, policy_data in dict(metadata.get("gate_policies") or {}).items()
        if isinstance(policy_data, dict)
    }
    for route_id, path_result in path_results.items():
        route_policy = dict(path_result.metadata.get("gate_policies") or {})
        for gate_id, policy_data in route_policy.items():
            if isinstance(policy_data, dict):
                policies.setdefault(gate_id, {}).update(policy_data)
        policies.setdefault("G2", {})
        policies["G2"].setdefault("min_points", 1)
        policies["G2"].setdefault("min_seed_candidates", 1)
    return GateContext(
        study=study,
        path_results=path_results,
        refinement_manifests=refinement_manifests,
        endpoint_matches=endpoint_matches,
        irc_results=irc_results,
        policies=policies,
    )


def check_g0(context: GateContext) -> QualityGateResult:
    study = context.study
    atom_map = study.atom_identity_map
    missing: list[str] = []
    evidence: dict[str, Any] = {
        "has_atom_identity_map": atom_map is not None,
        "route_count": len(study.routes),
    }
    thresholds = {"require_atom_identity_map": True, "require_compilable_coordinates": True}
    if atom_map is None or not atom_map.uid_to_structure_index:
        missing.append("atom_identity_map")
    else:
        evidence["n_atom_uids"] = len(atom_map.uid_to_structure_index)

    compilable = True
    max_index = (
        max(atom_map.uid_to_structure_index.values(), default=-1) if atom_map is not None else -1
    )
    invalid_refs: list[dict[str, Any]] = []
    for route in study.routes:
        for spec in route.coordinate_plan.coordinates:
            for atom_index in spec.atoms:
                if atom_index < 0 or atom_index > max_index:
                    compilable = False
                    invalid_refs.append(
                        {
                            "route_id": route.route_id,
                            "coordinate_id": spec.id,
                            "atom": atom_index,
                        }
                    )
    evidence["coordinate_refs_compilable"] = compilable
    evidence["invalid_coordinate_refs"] = invalid_refs
    status: GateStatus = "pass" if not missing and compilable else "fail"
    return QualityGateResult(
        gate_id="G0",
        status=status,
        evidence=evidence,
        thresholds=thresholds,
        missing_evidence=missing,
        suggested_action=(
            None
            if status == "pass"
            else "Provide a complete AtomIdentityMap and valid coordinate atom indices."
        ),
    )


def check_g1(context: GateContext) -> QualityGateResult:
    empty_states: list[str] = []
    ranking_missing: list[str] = []
    for state in context.study.stable_states:
        ensemble = state.ensemble
        if ensemble is None or len(ensemble.records) == 0:
            empty_states.append(state.state_id)
            continue
        if any(record.energy_hartree is None for record in ensemble.records):
            ranking_missing.append(state.state_id)
    status: GateStatus
    if empty_states:
        status = "fail"
    elif ranking_missing:
        status = "warn"
    else:
        status = "pass"
    return QualityGateResult(
        gate_id="G1",
        status=status,
        evidence={
            "state_count": len(context.study.stable_states),
            "empty_states": empty_states,
            "ranking_missing": ranking_missing,
        },
        thresholds={"min_conformers_per_state": 1},
        missing_evidence=empty_states,
        suggested_action=(
            None
            if status == "pass"
            else "Regenerate stable-state ensembles until every state has at least one conformer."
        ),
    )


def check_g2(context: GateContext) -> QualityGateResult:
    policy = dict(context.policies.get("G2") or {})
    policy.setdefault("require_complete", True)
    policy.setdefault("min_points", 1)
    policy.setdefault("min_seed_candidates", 1)
    policy.setdefault("require_endpoint_evidence", False)
    policy.setdefault("require_valid_topology", False)

    failures: list[str] = []
    warnings: list[str] = []
    evidence_routes: dict[str, Any] = {}
    for route_id, result in context.path_results.items():
        route_evidence = {
            "complete": bool(result.complete) if result.complete is not None else False,
            "n_points": len(result.points),
            "n_seed_candidates": len(result.seed_candidates),
            "has_endpoint_evidence": bool(result.endpoint_evidence),
            "all_topology_valid": all(point.topology_valid is not False for point in result.points),
        }
        evidence_routes[route_id] = route_evidence
        if policy["require_complete"] and not route_evidence["complete"]:
            failures.append(f"{route_id}:incomplete_path")
        if route_evidence["n_points"] < int(policy["min_points"]):
            failures.append(f"{route_id}:insufficient_points")
        if route_evidence["n_seed_candidates"] < int(policy["min_seed_candidates"]):
            failures.append(f"{route_id}:insufficient_seed_candidates")
        if (
            bool(policy["require_endpoint_evidence"])
            and not route_evidence["has_endpoint_evidence"]
        ):
            failures.append(f"{route_id}:missing_endpoint_evidence")
        if bool(policy["require_valid_topology"]) and not route_evidence["all_topology_valid"]:
            failures.append(f"{route_id}:topology_invalid")
        elif not route_evidence["all_topology_valid"]:
            warnings.append(f"{route_id}:topology_invalid")
    status: GateStatus = (
        "fail" if failures else ("warn" if warnings or not context.path_results else "pass")
    )
    if not context.path_results:
        status = "warn"
        warnings.append("no_path_results")
    return QualityGateResult(
        gate_id="G2",
        status=status,
        evidence={"routes": evidence_routes, "warnings": warnings},
        thresholds=policy,
        missing_evidence=failures,
        suggested_action=(
            None if status == "pass" else "Adjust path-strategy policy or regenerate path evidence."
        ),
    )


def check_g3(context: GateContext) -> QualityGateResult:
    policy = dict(context.policies.get("G3") or {})
    policy.setdefault("require_ts_identity", True)
    policy.setdefault("allow_unvalidated_minima", True)
    failures: list[str] = []
    evidence: dict[str, Any] = {"stationary_points": []}
    for point in context.study.stationary_points:
        point_evidence = {
            "point_id": point.point_id,
            "kind": point.kind,
            "has_identity": point.identity is not None,
            "identity_valid": point.identity.valid if point.identity is not None else None,
        }
        evidence["stationary_points"].append(point_evidence)
        if point.kind == "ts" and bool(policy["require_ts_identity"]):
            if point.identity is None or not point.identity.valid:
                failures.append(point.point_id)
        if point.kind == "minimum" and not bool(policy["allow_unvalidated_minima"]):
            validated = point.metadata.get("validated", False)
            if not validated:
                failures.append(point.point_id)
    status: GateStatus = (
        "fail" if failures else ("warn" if not context.study.stationary_points else "pass")
    )
    if not context.study.stationary_points:
        failures = ["stationary_points"]
    return QualityGateResult(
        gate_id="G3",
        status=status,
        evidence=evidence,
        thresholds=policy,
        missing_evidence=failures,
        suggested_action=(
            None
            if status == "pass"
            else "Refine stationary points until TS/INT validation evidence is complete."
        ),
    )


def check_g4(context: GateContext) -> QualityGateResult:
    unresolved_decisions = [
        decision.id for decision in context.study.decision_points if decision.status == "waiting"
    ]
    incomplete_irc = [
        irc_id for irc_id, irc in context.irc_results.items() if not (irc.success and irc.complete)
    ]
    failed_matches = [
        match_id
        for match_id, match in context.endpoint_matches.items()
        if match.verdict in {"AMBIGUOUS", "FAILED"}
    ]
    missing_connectivity = []
    for edge in context.study.elementary_steps:
        if (
            edge.source_state_id not in context.study.network.nodes
            or edge.sink_state_id not in context.study.network.nodes
        ):
            missing_connectivity.append(edge.step_id)
    failures = unresolved_decisions + incomplete_irc + failed_matches + missing_connectivity
    status: GateStatus = (
        "fail" if failures else ("warn" if not context.study.elementary_steps else "pass")
    )
    if not context.study.elementary_steps:
        failures = ["elementary_steps"]
    return QualityGateResult(
        gate_id="G4",
        status=status,
        evidence={
            "elementary_step_count": len(context.study.elementary_steps),
            "unresolved_decisions": unresolved_decisions,
            "incomplete_irc": incomplete_irc,
            "failed_matches": failed_matches,
            "missing_connectivity": missing_connectivity,
        },
        thresholds={"require_complete_irc": True, "allow_ambiguous": False},
        missing_evidence=failures,
        suggested_action=(
            None
            if status == "pass"
            else "Resolve ambiguous endpoint classifications and complete IRC connectivity."
        ),
    )


def check_g5(context: GateContext) -> QualityGateResult:
    policy = dict(context.policies.get("G5") or {})
    policy.setdefault("require_high_fidelity", False)
    high_fidelity = context.study.metadata.get("high_fidelity")
    quality = context.study.quality
    if quality == "high" or (quality is None and high_fidelity):
        status: GateStatus = "pass"
        missing: list[str] = []
        suggested_action = None
    elif bool(policy["require_high_fidelity"]):
        status = "fail"
        missing = ["high_fidelity_confirmation"]
        suggested_action = (
            "Study retained at medium quality without complete S4 confirmation; rerun the "
            "S4 high-fidelity loop to satisfy require_high_fidelity."
        )
    else:
        status = "warn"
        missing = ["high_fidelity_confirmation"]
        suggested_action = (
            "Study retained at medium quality without complete S4 confirmation; rerun the "
            "S4 high-fidelity loop when high-fidelity confirmation is required."
        )
    return QualityGateResult(
        gate_id="G5",
        status=status,
        evidence={"quality": quality, "high_fidelity": high_fidelity},
        thresholds=policy,
        missing_evidence=missing,
        suggested_action=suggested_action,
    )


_GATE_CHECKS = {
    "G0": check_g0,
    "G1": check_g1,
    "G2": check_g2,
    "G3": check_g3,
    "G4": check_g4,
    "G5": check_g5,
}


def run_gates(study: MechanismStudy, upto: str | None = None) -> list[QualityGateResult]:
    """Run quality gates in order and persist ``quality_gates.json``."""
    context = _build_context(study)
    results: list[QualityGateResult] = []
    for gate_id in GATE_IDS:
        results.append(_GATE_CHECKS[gate_id](context))
        if upto == gate_id:
            break
    study.quality_gates = results
    if study.study_dir:
        output_path = Path(study.study_dir) / "quality_gates.json"
        _write_json_atomic(
            output_path,
            {"quality_gates": [result.to_dict() for result in results]},
        )
    return results


__all__ = ["GATE_IDS", "GateContext", "run_gates"]
