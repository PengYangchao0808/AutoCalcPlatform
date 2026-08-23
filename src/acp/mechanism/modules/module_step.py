"""``mech-step`` module runner: elementary step path → refine → IRC (M2)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np

from cccp.qc.interfaces.constraints import ReactionCoordinatePlan
from cccp.utils.file_io import read_xyz, write_xyz

from .._helpers import fingerprint
from ..endpoint import DefaultEndpointProvider, EndpointMatchThresholds
from ..engines.elementary_step import ElementaryStepEngine, StepOutcome
from ..models import (
    ArtifactRef,
    AtomIdentityMap,
    MechanismRoute,
    MechanismStudy,
    StableState,
)
from ..presets import FIDELITY_PROFILES, resolve_fidelity
from ..providers.contracts import EndpointMatchResult, IrcResult
from .schema import (
    ElementaryStepManifest,
    EndpointDirection,
    FailureRecord,
    ResolvedEndpoint,
    step_top_status,
    write_elementary_step_manifest,
)

logger = logging.getLogger(__name__)


def _minimal_state(
    state_id: str,
    role: Literal["reactant", "product", "intermediate"],
    xyz_path: str,
    charge: int,
    multiplicity: int,
) -> StableState:
    coordinates, symbols = read_xyz(Path(xyz_path))
    return StableState(
        state_id=state_id,
        role=role,
        canonical_geometry=ArtifactRef(
            path=str(xyz_path),
            sha256=fingerprint({"xyz": str(xyz_path)}),
            kind="input_geometry",
        ),
        charge=charge,
        multiplicity=multiplicity,
        identity_fingerprint="",
        metadata={
            "symbols": [str(symbol) for symbol in symbols],
            "coordinates": np.asarray(coordinates, dtype=float).tolist(),
            "charge": charge,
            "multiplicity": multiplicity,
        },
    )


def _default_providers(
    strategy: str,
    config: dict[str, Any] | None,
    calc_dir: Path,
) -> dict[str, Any]:
    from ..providers.guided_scan import GuidedScanPathStrategy
    from ..providers.native_peb import NativeReversePebStrategy
    from ..providers.native_refinement import NativeRefinementProvider

    if strategy == "rph-reverse":
        path_strategy: Any = NativeReversePebStrategy(config, work_root=calc_dir / "s2_peb")
    elif strategy == "guided-scan":
        path_strategy = GuidedScanPathStrategy(config=config, work_root=calc_dir / "s2")
    else:
        raise ValueError(f"Unsupported mech-step strategy: {strategy!r}")
    from acp.backends import get_backend

    endpoint_provider = DefaultEndpointProvider(
        backend=get_backend("orca")(config),
        thresholds=EndpointMatchThresholds(),
        work_root=calc_dir / "sr",
    )
    return {
        "path_strategy": path_strategy,
        "refinement_provider": NativeRefinementProvider(config=config, work_root=calc_dir / "s3s4"),
        "endpoint_provider": endpoint_provider,
    }


def _resolve_step_endpoints(
    irc_result: IrcResult,
    endpoint_match: EndpointMatchResult | None,
    source_state: StableState,
) -> dict[str, ResolvedEndpoint]:
    """Resolve forward/reverse IRC endpoints into dynamic source/sink roles."""
    evidence = dict(endpoint_match.evidence) if endpoint_match is not None else {}
    sink_direction = str(evidence.get("sink_direction") or "")
    source_direction = str(evidence.get("source_direction") or "")
    if sink_direction not in ("forward", "reverse"):
        sink_direction = "reverse" if source_direction == "forward" else "forward"
    if source_direction not in ("forward", "reverse"):
        source_direction = "reverse" if sink_direction == "forward" else "forward"

    verdict = endpoint_match.verdict if endpoint_match is not None else "FAILED"
    matched_state_id = endpoint_match.state_id if endpoint_match is not None else None
    minimum_validated = False
    minimum_summary = evidence.get("minimum_validation")
    if isinstance(minimum_summary, dict):
        minimum_validated = minimum_summary.get("status") == "validated"

    resolved: dict[str, ResolvedEndpoint] = {}
    for direction, artifact in (
        ("forward", irc_result.forward_endpoint),
        ("reverse", irc_result.reverse_endpoint),
    ):
        if artifact is None:
            continue
        endpoint_direction = cast(EndpointDirection, direction)
        if direction == source_direction:
            resolved[direction] = ResolvedEndpoint(
                endpoint_id=f"{irc_result.irc_id}_{direction}",
                direction=endpoint_direction,
                role="source",
                raw_geometry=artifact,
                minimum_validated=False,
                match_verdict="MATCH_EXISTING",
                matched_state_id=source_state.state_id,
                evidence={},
            )
        else:
            resolved[direction] = ResolvedEndpoint(
                endpoint_id=f"{irc_result.irc_id}_{direction}",
                direction=endpoint_direction,
                role="sink",
                raw_geometry=artifact,
                minimum_validated=minimum_validated,
                match_verdict=verdict,
                matched_state_id=matched_state_id,
                evidence=evidence,
            )
    return resolved


def _write_ts_xyz(output_dir: Path, outcome: StepOutcome) -> str:
    ts = outcome.canonical_ts
    assert ts is not None
    geometry_path = Path(ts.geometry.path)
    target = output_dir / "ts_canonical.xyz"
    if geometry_path.exists() and geometry_path.suffix.lower() == ".xyz":
        target.write_text(geometry_path.read_text(encoding="utf-8"), encoding="utf-8")
        return str(target)
    coordinates = ts.metadata.get("coordinates")
    symbols = ts.metadata.get("symbols")
    if coordinates is None or symbols is None:
        return ts.geometry.path
    write_xyz(
        target,
        np.asarray(coordinates, dtype=float),
        [str(s) for s in symbols],
        title=f"canonical TS {ts.point_id}",
    )
    return str(target)


def _ts_validation_block(outcome: StepOutcome) -> dict[str, Any]:
    ts = outcome.canonical_ts
    if ts is None:
        return {}
    identity = ts.identity
    return {
        "optimization_converged": bool(ts.metadata.get("confirmed", True)),
        "hessian_index": identity.imaginary_count if identity is not None else None,
        "mode_match_passed": bool(identity.valid) if identity is not None else False,
        "imaginary_frequency_cm1": (
            identity.imaginary_frequency_cm1 if identity is not None else None
        ),
    }


def run_step_module(
    source_xyz: str,
    coordinate_plan: dict[str, Any],
    *,
    target_xyz: str | None = None,
    strategy: str = "rph-reverse",
    fidelity: str = "s3",
    endpoint_method: str = "irc",
    output_dir: Path | str,
    config: dict[str, Any] | None = None,
    charge: int = 0,
    multiplicity: int = 1,
    label: str | None = None,
    providers: dict[str, Any] | None = None,
) -> ElementaryStepManifest:
    """Run one elementary step and persist the ElementaryStepManifest."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    step_id = f"step_{label or 'module'}"
    calc_dir = out / "calc"

    source_state = _minimal_state("s1", "reactant", source_xyz, charge, multiplicity)
    target_state = (
        _minimal_state("s2", "product", target_xyz, charge, multiplicity) if target_xyz else None
    )
    plan = ReactionCoordinatePlan.from_dict(coordinate_plan)
    route = MechanismRoute(
        route_id="route_1",
        coordinate_plan=plan,
        path_strategy=strategy,
        fidelity=fidelity,
        reactant_id=source_state.state_id,
        product_id=target_state.state_id if target_state is not None else None,
    )
    study = MechanismStudy(
        study_id=f"module_{label or 'step'}",
        stable_states=[s for s in (source_state, target_state) if s is not None],
        routes=[route],
        atom_identity_map=AtomIdentityMap(uid_to_structure_index={}, mapping={}),
    )

    bundle = providers or _default_providers(strategy, config, calc_dir)
    engine = ElementaryStepEngine(
        study=study,
        layout=out,
        path_strategy=bundle["path_strategy"],
        refinement_provider=bundle["refinement_provider"],
        endpoint_provider=bundle["endpoint_provider"],
        ensemble_profile="censo-lite",
        low_fidelity_profile=FIDELITY_PROFILES[resolve_fidelity(fidelity)],
    )
    try:
        outcome = engine.run(engine.prepare(source_state, route, 0))
    except Exception as exc:
        logger.exception("mech-step failed: %s", exc)
        manifest = ElementaryStepManifest(
            step_id=step_id,
            status="failed",
            furthest_stage="path",
            coordinate_plan=dict(coordinate_plan),
            failure=FailureRecord(
                stage="path",
                reason="elementary_step_engine_error",
                recoverable=True,
                details={"error": str(exc)},
            ),
            suggested_actions=["retry_refinement", "change_seed", "manual_takeover"],
        )
        write_elementary_step_manifest(out, manifest)
        return manifest

    manifest = _build_step_manifest(
        step_id=step_id,
        route=route,
        outcome=outcome,
        source_state=source_state,
        target_state=target_state,
        coordinate_plan=coordinate_plan,
        strategy=strategy,
        fidelity=fidelity,
        endpoint_method=endpoint_method,
        charge=charge,
        multiplicity=multiplicity,
        output_dir=out,
    )
    write_elementary_step_manifest(out, manifest)
    return manifest


def _build_step_manifest(
    *,
    step_id: str,
    route: MechanismRoute,
    outcome: StepOutcome,
    source_state: StableState,
    target_state: StableState | None,
    coordinate_plan: dict[str, Any],
    strategy: str,
    fidelity: str,
    endpoint_method: str,
    charge: int,
    multiplicity: int,
    output_dir: Path,
) -> ElementaryStepManifest:
    route_dir = output_dir / "routes" / f"{source_state.state_id}__{route.route_id}"
    path_block: dict[str, Any] = {
        "path_result": str(route_dir / "path_manifest.json"),
        "route_id": route.route_id,
        "seed_candidates": [seed.id for seed in outcome.path_result.seed_candidates],
        "complete": outcome.path_result.complete,
    }
    gates = {
        "G2": "PASS" if outcome.path_result.complete else "FAIL",
        "G3": "PASS" if outcome.canonical_ts is not None else "FAIL",
        "G4": (
            "PASS"
            if outcome.endpoint_match is not None
            and outcome.endpoint_match.verdict in ("MATCH_EXISTING", "NEW_STATE")
            else "FAIL"
        ),
    }

    if outcome.canonical_ts is None:
        return ElementaryStepManifest(
            step_id=step_id,
            status="partial",
            target_state_id=target_state.state_id if target_state is not None else None,
            furthest_stage="refinement",
            coordinate_plan=dict(coordinate_plan),
            method={"strategy": strategy, "fidelity": fidelity, "endpoint_method": endpoint_method},
            path=path_block,
            transition_state=None,
            irc=None,
            gates=gates,
            failure=FailureRecord(
                stage="refinement",
                reason="no_canonical_stationary_point",
                recoverable=True,
            ),
            suggested_actions=["retry_refinement", "change_seed", "manual_takeover"],
            provenance=_step_provenance(
                coordinate_plan, strategy, fidelity, charge, multiplicity, source_state
            ),
        )

    ts_xyz = _write_ts_xyz(output_dir, outcome)
    transition_state: dict[str, Any] = {
        "canonical_id": outcome.canonical_ts.point_id,
        "xyz": ts_xyz,
        "point": outcome.canonical_ts.to_dict(),
        "validation": _ts_validation_block(outcome),
    }
    irc_block: dict[str, Any] | None = None
    if outcome.irc_result is not None:
        irc_block = {
            "irc_id": outcome.irc_result.irc_id,
            "complete": outcome.irc_result.complete,
            "endpoints": {
                direction: resolved.to_dict()
                for direction, resolved in _resolve_step_endpoints(
                    outcome.irc_result, outcome.endpoint_match, source_state
                ).items()
            },
        }

    if outcome.needs_review:
        status = step_top_status("AMBIGUOUS_ENDPOINT")
    elif all(value == "PASS" for value in gates.values()):
        status = step_top_status("VALIDATED")
    else:
        status = step_top_status(None)
    return ElementaryStepManifest(
        step_id=step_id,
        status=status,
        target_state_id=target_state.state_id if target_state is not None else None,
        furthest_stage="endpoint_validation" if irc_block is not None else "ts_validation",
        coordinate_plan=dict(coordinate_plan),
        method={"strategy": strategy, "fidelity": fidelity, "endpoint_method": endpoint_method},
        path=path_block,
        transition_state=transition_state,
        irc=irc_block,
        gates=gates,
        provenance=_step_provenance(
            coordinate_plan, strategy, fidelity, charge, multiplicity, source_state
        ),
    )


def _step_provenance(
    coordinate_plan: dict[str, Any],
    strategy: str,
    fidelity: str,
    charge: int,
    multiplicity: int,
    source_state: StableState,
) -> dict[str, Any]:
    return {
        "parent_manifest": None,
        "engine": "acp-elementary-step-engine",
        "phases": {"path": "S2", "refinement": "S3", "irc": "SR"},
        "charge": charge,
        "multiplicity": multiplicity,
        "source_state_id": source_state.state_id,
        "fingerprint": fingerprint(
            {
                "coordinate_plan": coordinate_plan,
                "strategy": strategy,
                "fidelity": fidelity,
                "charge": charge,
                "multiplicity": multiplicity,
                "source_state_id": source_state.state_id,
            }
        ),
    }


__all__ = ["run_step_module"]
