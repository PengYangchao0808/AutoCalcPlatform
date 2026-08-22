"""Elementary Step Engine — the single implementation of path→refine→IRC→endpoint.

Extracted from :class:`acp.mechanism.orchestrator.StudyOrchestrator` so the
per-route elementary-step computation exists in exactly one place. Both the
Study layer (``StudyOrchestrator._run_frontier_loop``) and the standalone
``mech-step`` module delegate here — there is no second copy of the science.

The engine computes evidence only. Network application (registering NEW_STATE
endpoints, adding elementary-step edges, creating review decisions) stays in
the Study layer.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from acp.core.state import EventLog

from .._helpers import fingerprint
from .._helpers import write_json_atomic as _write_json_atomic
from ..models import (
    ArtifactRef,
    MechanismRoute,
    MechanismStudy,
    PathPoint,
    PathResult,
    Provenance,
    SeedCandidate,
    StableState,
    StationaryPoint,
    StationaryPointRequest,
)
from ..providers.contracts import (
    EndpointMatchResult,
    EndpointProvider,
    IrcResult,
    PathSearchStrategy,
    RefinementManifest,
    RefinementProvider,
)

logger = logging.getLogger(__name__)

ReviewPolicy = Callable[[EndpointMatchResult, MechanismStudy, dict[str, Any]], bool]


# ---------------------------------------------------------------------------
# Shared route-level bookkeeping (single implementation for orchestrator +
# engine + standalone modules)
# ---------------------------------------------------------------------------


def exploration_key(source_state_id: str, route_id: str) -> str:
    return f"{source_state_id}::{route_id}"


def route_fingerprint(
    study: MechanismStudy,
    source_state: StableState,
    route: MechanismRoute,
    target_state: StableState | None,
    depth: int,
) -> str:
    return fingerprint(
        {
            "source_state_id": source_state.state_id,
            "target_state_id": target_state.state_id if target_state is not None else None,
            "route": route.to_dict(),
            "depth": depth,
            "input_signature": study.metadata.get("input_signature"),
        }
    )


def route_status_matches(
    study: MechanismStudy,
    exploration_key: str,
    route_fingerprint: str,
    statuses: set[str],
) -> bool:
    route_statuses = dict(study.metadata.get("route_statuses") or {})
    record = route_statuses.get(exploration_key)
    if not isinstance(record, dict):
        return False
    return (
        str(record.get("fingerprint") or "") == route_fingerprint
        and str(record.get("status") or "") in statuses
    )


def mark_route_status(
    study: MechanismStudy,
    exploration_key: str,
    route_fingerprint: str,
    *,
    status: str,
) -> None:
    route_statuses = dict(study.metadata.get("route_statuses") or {})
    route_statuses[exploration_key] = {
        "fingerprint": route_fingerprint,
        "status": status,
        "updated_at": _utc_now(),
    }
    study.metadata["route_statuses"] = route_statuses


def persist_route_manifest(
    study_dir: Path,
    *,
    source_state_id: str,
    route: MechanismRoute,
    route_fingerprint: str,
    status: str,
    path_result: PathResult | None = None,
    refinement_manifest: RefinementManifest | None = None,
    irc_result: IrcResult | None = None,
    endpoint_match: EndpointMatchResult | None = None,
    depth: int | None = None,
) -> None:
    route_dir = study_dir / "routes" / f"{source_state_id}__{route.route_id}"
    payload: dict[str, Any] = {
        "source_state_id": source_state_id,
        "route_id": route.route_id,
        "route": route.to_dict(),
        "fingerprint": route_fingerprint,
        "status": status,
        "depth": depth,
    }
    if path_result is not None:
        payload["path_result"] = path_result.to_dict()
    if refinement_manifest is not None:
        payload["refinement_manifest"] = refinement_manifest.to_dict()
    if irc_result is not None:
        payload["irc_result"] = irc_result.to_dict()
    if endpoint_match is not None:
        payload["endpoint_match"] = endpoint_match.to_dict()
    _write_json_atomic(route_dir / "path_manifest.json", payload)


def persist_refinement_manifest(study_dir: Path, manifest: RefinementManifest) -> None:
    ref_dir = study_dir / "refinements" / manifest.manifest_id
    _write_json_atomic(ref_dir / "refinement_manifest.json", manifest.to_dict())


def replace_seed_candidate(
    candidate_id: str,
    artifact: ArtifactRef,
    point: PathPoint,
) -> SeedCandidate:
    """Build a minimal SeedCandidate-like object from a fallback path candidate."""
    return SeedCandidate(
        id=candidate_id,
        kind="ts_seed",
        geometry=artifact,
        rank=1,
        selection_mode="path_candidate_fallback",
        confidence="medium",
        evidence={"point_id": point.point_id, "progress": point.progress},
    )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Route context / outcome
# ---------------------------------------------------------------------------


class RouteContext:
    """Resolved inputs for one elementary-step computation."""

    __slots__ = (
        "source_state",
        "route",
        "depth",
        "target_state",
        "route_fingerprint",
        "exploration_key",
    )

    def __init__(
        self,
        source_state: StableState,
        route: MechanismRoute,
        depth: int,
        target_state: StableState | None,
        route_fingerprint: str,
        exploration_key: str,
    ) -> None:
        self.source_state = source_state
        self.route = route
        self.depth = depth
        self.target_state = target_state
        self.route_fingerprint = route_fingerprint
        self.exploration_key = exploration_key


class StepOutcome:
    """Evidence produced by one elementary-step computation.

    ``needs_review`` / ``decision_type`` tell the Study layer whether a
    review decision must be created before applying the endpoint match.
    """

    __slots__ = (
        "path_result",
        "refinement_manifest",
        "canonical_ts",
        "irc_result",
        "endpoint_match",
        "needs_review",
        "decision_type",
    )

    def __init__(
        self,
        *,
        path_result: PathResult,
        refinement_manifest: RefinementManifest,
        canonical_ts: StationaryPoint | None,
        irc_result: IrcResult | None,
        endpoint_match: EndpointMatchResult | None,
        needs_review: bool,
        decision_type: str | None,
    ) -> None:
        self.path_result = path_result
        self.refinement_manifest = refinement_manifest
        self.canonical_ts = canonical_ts
        self.irc_result = irc_result
        self.endpoint_match = endpoint_match
        self.needs_review = needs_review
        self.decision_type = decision_type


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ElementaryStepEngine:
    """Run path search → coarse refinement → IRC → endpoint classification.

    Mirrors the previous per-route body of ``StudyOrchestrator._run_frontier_loop``
    exactly (events, metadata keys, persistence, fingerprints) so the Study
    layer observes identical behavior.
    """

    def __init__(
        self,
        study: MechanismStudy,
        study_dir: Path,
        *,
        path_strategy: PathSearchStrategy,
        refinement_provider: RefinementProvider,
        endpoint_provider: EndpointProvider,
        ensemble_profile: Any = "xtb-fast",
        low_fidelity_profile: Any = "s3",
        require_review: ReviewPolicy | None = None,
        require_sr_review: bool = False,
        event_log: EventLog | None = None,
    ) -> None:
        self.study = study
        self.study_dir = Path(study_dir)
        self.path_strategy = path_strategy
        self.refinement_provider = refinement_provider
        self.endpoint_provider = endpoint_provider
        self.ensemble_profile = ensemble_profile
        self.low_fidelity_profile = low_fidelity_profile
        self.require_review = require_review
        self.require_sr_review = require_sr_review
        self.event_log = event_log or EventLog(self.study_dir / "events.jsonl")

    # -- preparation ---------------------------------------------------------

    def prepare(
        self,
        source_state: StableState,
        route: MechanismRoute,
        depth: int,
    ) -> RouteContext:
        target_state = self.study.get_state(route.product_id) if route.product_id else None
        return RouteContext(
            source_state=source_state,
            route=route,
            depth=depth,
            target_state=target_state,
            route_fingerprint=route_fingerprint(
                self.study, source_state, route, target_state, depth
            ),
            exploration_key=exploration_key(source_state.state_id, route.route_id),
        )

    def already_done(self, ctx: RouteContext) -> bool:
        return route_status_matches(
            self.study,
            ctx.exploration_key,
            ctx.route_fingerprint,
            {"completed", "stopped"},
        )

    # -- computation ----------------------------------------------------------

    def run(self, ctx: RouteContext) -> StepOutcome:
        source_state = ctx.source_state
        route = ctx.route
        depth = ctx.depth

        self._emit_event(
            "S2",
            "path_search_started",
            source_state_id=source_state.state_id,
            route_id=route.route_id,
            depth=depth,
        )
        path_result = self.path_strategy.search(
            source_state,
            ctx.target_state,
            route.coordinate_plan,
            self.ensemble_profile,
        )
        self.study.metadata.setdefault("path_results", {})[ctx.exploration_key] = (
            path_result.to_dict()
        )
        self._merge_gate_policies(path_result)
        persist_route_manifest(
            self.study_dir,
            source_state_id=source_state.state_id,
            route=route,
            route_fingerprint=ctx.route_fingerprint,
            status="searched",
            path_result=path_result,
            depth=depth,
        )
        self._emit_event(
            "S2",
            "path_search_completed",
            source_state_id=source_state.state_id,
            route_id=route.route_id,
            n_points=len(path_result.points),
        )

        refinement_requests = self._build_refinement_requests(source_state, route, path_result)
        self._emit_event(
            "S3",
            "refinement_started",
            source_state_id=source_state.state_id,
            route_id=route.route_id,
            n_requests=len(refinement_requests),
        )
        manifest = self.refinement_provider.refine(refinement_requests, self.low_fidelity_profile)
        self.study.metadata.setdefault("refinement_manifests", {})[manifest.manifest_id] = (
            manifest.to_dict()
        )
        persist_refinement_manifest(self.study_dir, manifest)
        canonical_ts = manifest.canonical_winner
        if canonical_ts is None:
            mark_route_status(
                self.study, ctx.exploration_key, ctx.route_fingerprint, status="failed"
            )
            persist_route_manifest(
                self.study_dir,
                source_state_id=source_state.state_id,
                route=route,
                route_fingerprint=ctx.route_fingerprint,
                status="failed",
                path_result=path_result,
                refinement_manifest=manifest,
                depth=depth,
            )
            return StepOutcome(
                path_result=path_result,
                refinement_manifest=manifest,
                canonical_ts=None,
                irc_result=None,
                endpoint_match=None,
                needs_review=False,
                decision_type=None,
            )
        self._upsert_stationary_point(canonical_ts)
        self._emit_event(
            "S3",
            "refinement_completed",
            manifest_id=manifest.manifest_id,
            ts_id=canonical_ts.point_id,
        )
        self._enrich_ts_for_irc(canonical_ts, source_state, path_result)

        self._emit_event(
            "SR",
            "irc_started",
            source_state_id=source_state.state_id,
            route_id=route.route_id,
            ts_id=canonical_ts.point_id,
        )
        irc_result = self.endpoint_provider.run_irc(canonical_ts, self.low_fidelity_profile)
        endpoint_match = self.endpoint_provider.classify_endpoints(
            irc_result,
            self.study.stable_states,
        )
        self.study.metadata.setdefault("irc_results", {})[irc_result.irc_id] = irc_result.to_dict()
        self.study.metadata.setdefault("endpoint_matches", {})[ctx.exploration_key] = (
            endpoint_match.to_dict()
        )

        needs_review = self._needs_review(
            endpoint_match,
            source_state_id=source_state.state_id,
            route_id=route.route_id,
            depth=depth,
        )
        decision_type = (
            "mechanism_frontier_review"
            if endpoint_match.verdict == "AMBIGUOUS"
            else "sr_cycle_review"
        )
        return StepOutcome(
            path_result=path_result,
            refinement_manifest=manifest,
            canonical_ts=canonical_ts,
            irc_result=irc_result,
            endpoint_match=endpoint_match,
            needs_review=needs_review,
            decision_type=decision_type,
        )

    # -- compute helpers -------------------------------------------------------

    def _build_refinement_requests(
        self,
        source_state: StableState,
        route: MechanismRoute,
        path_result: PathResult,
    ) -> list[StationaryPointRequest]:
        requests: list[StationaryPointRequest] = []
        ts_seeds = [seed for seed in path_result.seed_candidates if seed.kind == "ts_seed"]
        if not ts_seeds:
            for candidate in path_result.candidates:
                if candidate.kind != "ts_seed":
                    continue
                point = path_result.point_by_id(candidate.point_id)
                if point is None:
                    continue
                artifact = self._materialize_point_artifact(
                    source_state.state_id,
                    route.route_id,
                    point,
                )
                ts_seeds.append(
                    replace_seed_candidate(
                        candidate_id=candidate.candidate_id,
                        artifact=artifact,
                        point=point,
                    )
                )
        if not ts_seeds:
            raise ValueError("PathResult produced no TS seed candidates")

        provenance = self._request_provenance(path_result, source_state, route)
        for seed in ts_seeds:
            input_geometry = seed.geometry
            point_id = str(seed.evidence.get("point_id") or "")
            if not Path(seed.geometry.path).exists() and point_id:
                point = path_result.point_by_id(point_id)
                if point is not None:
                    input_geometry = self._materialize_point_artifact(
                        source_state.state_id,
                        route.route_id,
                        point,
                    )
            requests.append(
                StationaryPointRequest(
                    id=seed.id,
                    role="transition_state",
                    kind="ts",
                    input_geometry=input_geometry,
                    coordinate_plan=route.coordinate_plan,
                    fallback_geometries=[],
                    source_stage="S2",
                    charge=source_state.charge,
                    multiplicity=source_state.multiplicity,
                    atom_mapping=self.study.atom_identity_map,
                    parent_state_id=source_state.state_id,
                    route_id=route.route_id,
                    ensemble_correction=None,
                    provenance=provenance,
                )
            )
        return requests

    def _materialize_point_artifact(
        self,
        state_id: str,
        route_id: str,
        point: PathPoint,
    ) -> ArtifactRef:
        seed_path = (
            self.study_dir
            / "routes"
            / f"{state_id}__{route_id}"
            / "seeds"
            / f"{point.point_id}.json"
        )
        payload = point.to_dict()
        _write_json_atomic(seed_path, payload)
        return ArtifactRef(path=str(seed_path), sha256=fingerprint(payload), kind="path_point")

    def _request_provenance(
        self,
        path_result: PathResult,
        source_state: StableState,
        route: MechanismRoute,
    ) -> Provenance:
        if path_result.points and path_result.points[0].provenance is not None:
            return path_result.points[0].provenance
        return Provenance(
            provider="acp-elementary-step-engine",
            provider_version="1.0",
            provider_commit="m0.5",
            strategy=path_result.strategy_id or path_result.strategy,
            strategy_version=path_result.strategy_version or "1.0",
            profile_id=str(self.low_fidelity_profile),
            schema_version="m0",
            input_signature=fingerprint(
                {
                    "source_state_id": source_state.state_id,
                    "route": route.to_dict(),
                    "path_strategy": path_result.strategy,
                }
            ),
        )

    def _merge_gate_policies(self, path_result: PathResult) -> None:
        policies = dict(self.study.metadata.get("gate_policies") or {})
        for gate_id, policy in dict(path_result.metadata.get("gate_policies") or {}).items():
            if not isinstance(policy, dict):
                continue
            merged = dict(policies.get(gate_id) or {})
            merged.update(policy)
            policies[gate_id] = merged
        self.study.metadata["gate_policies"] = policies

    def _upsert_stationary_point(self, point: StationaryPoint) -> None:
        for index, current in enumerate(self.study.stationary_points):
            if current.point_id == point.point_id:
                self.study.stationary_points[index] = point
                return
        self.study.stationary_points.append(point)

    def _enrich_ts_for_irc(
        self,
        point: StationaryPoint,
        source_state: StableState,
        path_result: PathResult,
    ) -> None:
        symbols = point.metadata.get("symbols")
        if symbols is None:
            symbols = source_state.metadata.get("symbols")
        if symbols is not None:
            point.metadata.setdefault("symbols", list(symbols))
        if point.metadata.get("coordinates") is None:
            seed_id = point.point_id.rsplit("_", 1)[0]
            point_id = str(point.metadata.get("point_id") or "")
            if not point_id:
                for candidate in path_result.seed_candidates:
                    if candidate.id == seed_id:
                        point_id = str(candidate.evidence.get("point_id") or "")
                        break
            if point_id:
                path_point = path_result.point_by_id(point_id)
                if path_point is not None and path_point.geometry is not None:
                    point.metadata["coordinates"] = np.asarray(
                        path_point.geometry,
                        dtype=float,
                    ).tolist()

    def _needs_review(self, endpoint_match: EndpointMatchResult, **context: Any) -> bool:
        if endpoint_match.verdict == "AMBIGUOUS":
            return True
        if self.require_sr_review:
            return True
        if self.require_review is None:
            return False
        return bool(self.require_review(endpoint_match, self.study, context))

    def _emit_event(self, phase: str, action: str, **payload: Any) -> None:
        event = {"phase": phase, "action": action, **payload}
        self.event_log.append(event)


__all__ = [
    "ElementaryStepEngine",
    "RouteContext",
    "StepOutcome",
    "exploration_key",
    "mark_route_status",
    "persist_refinement_manifest",
    "persist_route_manifest",
    "replace_seed_candidate",
    "route_fingerprint",
    "route_status_matches",
]
