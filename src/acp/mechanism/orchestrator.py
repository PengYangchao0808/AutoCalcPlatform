# pyright: reportMissingImports=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportExplicitAny=false, reportUnusedCallResult=false, reportUnnecessaryComparison=false
"""Study orchestrator for contract-first mechanism exploration."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from acp.core.state import EventLog

from ._helpers import write_json_atomic as _write_json_atomic
from .endpoint import DefaultEndpointProvider, EndpointMatchThresholds
from .gates import run_gates
from .models import (
    ArtifactRef,
    DecisionPoint,
    ElementaryStepEdge,
    MechanismRoute,
    MechanismStudy,
    PathPoint,
    PathResult,
    Provenance,
    ReactionNetwork,
    SeedCandidate,
    StableState,
    StationaryPoint,
    StationaryPointRequest,
)
from .providers.contracts import (
    EndpointMatchResult,
    EndpointProvider,
    EnsembleProvider,
    IrcResult,
    PathSearchStrategy,
    RefinementManifest,
    RefinementProvider,
    ThermochemistryProvider,
)

logger = logging.getLogger(__name__)

ReviewPolicy = Callable[[EndpointMatchResult, MechanismStudy, dict[str, Any]], bool]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected in {path}")
    return payload


class StudyOrchestrator:
    """Provider-agnostic mechanism-study orchestrator.

    The orchestrator persists a study-level checkpoint tree under
    ``mechanism_study/<study_id>/`` and drives the top-level phases
    S0 → S1 → S2 → S3 → SR → S4 with a frontier-based non-recursive network
    expansion loop.
    """

    def __init__(
        self,
        study: MechanismStudy,
        *,
        study_root: Path | str,
        ensemble_provider: EnsembleProvider,
        path_strategy: PathSearchStrategy,
        refinement_provider: RefinementProvider,
        endpoint_provider: EndpointProvider | None = None,
        endpoint_backend: Any | None = None,
        thermochemistry_provider: ThermochemistryProvider | None = None,
        ensemble_profile: Any = "xtb-fast",
        low_fidelity_profile: Any = "s3",
        high_fidelity_profile: Any = "s4",
        max_elementary_steps: int = 5,
        require_review: ReviewPolicy | None = None,
        endpoint_match_thresholds: EndpointMatchThresholds | None = None,
        validate_endpoint_minimum: bool = True,
    ) -> None:
        self.study_template = MechanismStudy.from_dict(study.to_dict())
        self.study_root = Path(study_root)
        self.study_dir = self._resolve_study_dir(self.study_root, study.study_id)
        self.ensemble_provider = ensemble_provider
        self.path_strategy = path_strategy
        self.refinement_provider = refinement_provider
        self.endpoint_provider = endpoint_provider or DefaultEndpointProvider(
            backend=endpoint_backend,
            thresholds=endpoint_match_thresholds,
            validate_minimum=validate_endpoint_minimum,
            work_root=self.study_dir / "sr",
        )
        self.thermochemistry_provider = thermochemistry_provider
        self.ensemble_profile = ensemble_profile
        self.low_fidelity_profile = low_fidelity_profile
        self.high_fidelity_profile = high_fidelity_profile
        self.max_elementary_steps = max_elementary_steps
        self.require_review = require_review
        self.event_log = EventLog(self.study_dir / "events.jsonl")
        self.study: MechanismStudy = self._load_or_initialize_study()

    def run(self) -> MechanismStudy:
        """Run or resume a study from checkpoint when fingerprints match."""
        self.study = self._load_or_initialize_study()
        input_signature = self._input_signature(self.study_template)
        existing_signature = str(self.study.metadata.get("input_signature") or "")
        if existing_signature and existing_signature != input_signature:
            raise ValueError(
                "Existing study checkpoint fingerprint does not match the requested inputs"
            )
        self.study.metadata["input_signature"] = input_signature

        if self.study.status == "completed":
            return self.study
        if self.study.status == "waiting":
            return self.study

        self._run_phase_s0()
        self._run_phase_s1_initial()
        self._run_frontier_loop()
        if self.study.status != "waiting":
            self._run_phase_s4()
            self.study.status = "completed"
            self._emit_event("S4", "phase_completed", status=self.study.status)
        self._persist_study_bundle()
        run_gates(self.study)
        self._persist_study_bundle()
        return self.study

    def resume(self, decision_resolutions: dict[str, Any]) -> MechanismStudy:
        """Resolve persisted DecisionPoints and continue the frontier loop."""
        self.study = self._load_or_initialize_study()
        pending = dict(self.study.metadata.get("pending_decisions") or {})
        if not pending:
            return self.study

        for decision in self.study.decision_points:
            if decision.status != "waiting":
                continue
            resolution = decision_resolutions.get(decision.id)
            if resolution is None:
                continue
            action, resolution_payload = self._parse_resolution(resolution)
            context = pending.get(decision.id)
            if not isinstance(context, dict):
                continue
            if action == "stop_branch":
                decision.status = "resolved"
                decision.resolution = "stop_branch"
                decision.resolved_at = _utc_now()
                self._mark_route_status(
                    str(context.get("exploration_key") or ""),
                    str(context.get("route_fingerprint") or ""),
                    status="stopped",
                )
                pending.pop(decision.id, None)
                self._persist_decision(decision)
                continue
            if action in {"continue", "promote_to_s4"}:
                manifest = RefinementManifest.from_dict(
                    dict(context.get("refinement_manifest") or {})
                )
                canonical_ts = manifest.canonical_winner
                if canonical_ts is None:
                    raise ValueError("Pending decision missing canonical TS")
                source_state = self._require_state(str(context.get("source_state_id") or ""))
                route = self._require_route(str(context.get("route_id") or ""))
                depth = int(context.get("depth") or 0)
                irc_result = IrcResult.from_dict(dict(context.get("irc_result") or {}))
                match = EndpointMatchResult.from_dict(dict(context.get("endpoint_match") or {}))
                match_dict = match.to_dict()
                if match.verdict == "AMBIGUOUS":
                    match_dict["verdict"] = "NEW_STATE"
                candidate_override = resolution_payload.get("candidate_state")
                if candidate_override is not None:
                    evidence = dict(match_dict.get("evidence") or {})
                    evidence["candidate_state"] = candidate_override
                    match_dict["evidence"] = evidence
                match = EndpointMatchResult.from_dict(match_dict)
                self._apply_endpoint_match(
                    source_state=source_state,
                    route=route,
                    depth=depth,
                    route_fingerprint=str(context.get("route_fingerprint") or ""),
                    canonical_ts=canonical_ts,
                    irc_result=irc_result,
                    endpoint_match=match,
                )
                decision.status = "resolved"
                decision.resolution = action
                decision.resolved_at = _utc_now()
                pending.pop(decision.id, None)
                self._persist_decision(decision)
                continue
            if action == "edit_route":
                route = self._require_route(str(context.get("route_id") or ""))
                updated_route = self._updated_route(route, resolution_payload)
                self._replace_route(updated_route)
                source_state_id = str(context.get("source_state_id") or "")
                depth = int(context.get("depth") or 0)
                self.study.frontier.push(source_state_id, updated_route.route_id, depth=depth)
                decision.status = "resolved"
                decision.resolution = "edit_route"
                decision.resolved_at = _utc_now()
                pending.pop(decision.id, None)
                self._persist_decision(decision)

        self.study.metadata["pending_decisions"] = pending
        if any(decision.status == "waiting" for decision in self.study.decision_points):
            self.study.status = "waiting"
            self._persist_study_bundle()
            run_gates(self.study)
            self._persist_study_bundle()
            return self.study

        self.study.status = "running"
        self._emit_event("SR", "resume", resolved=list(decision_resolutions))
        self._persist_study_bundle()
        self._run_frontier_loop()
        if self.study.status != "waiting":
            self._run_phase_s4()
            self.study.status = "completed"
        self._persist_study_bundle()
        run_gates(self.study)
        self._persist_study_bundle()
        return self.study

    def _resolve_study_dir(self, root: Path, study_id: str) -> Path:
        if root.name == study_id and root.parent.name == "mechanism_study":
            return root
        return root / "mechanism_study" / study_id

    def _load_or_initialize_study(self) -> MechanismStudy:
        study_path = self.study_dir / "study.json"
        if study_path.exists():
            loaded = MechanismStudy.from_dict(_read_json(study_path))
            loaded.study_dir = str(self.study_dir)
            self._sync_network(loaded)
            return loaded
        study = MechanismStudy.from_dict(self.study_template.to_dict())
        study.study_dir = str(self.study_dir)
        study.metadata.setdefault("route_statuses", {})
        study.metadata.setdefault("path_results", {})
        study.metadata.setdefault("refinement_manifests", {})
        study.metadata.setdefault("endpoint_matches", {})
        study.metadata.setdefault("irc_results", {})
        study.metadata.setdefault("gate_policies", {})
        study.metadata.setdefault("pending_decisions", {})
        self._sync_network(study)
        return study

    def _persist_study_bundle(self) -> None:
        self.study.study_dir = str(self.study_dir)
        self._sync_network(self.study)
        _write_json_atomic(self.study_dir / "study.json", self.study.to_dict())
        _write_json_atomic(self.study_dir / "network.json", self.study.network.to_dict())

    def _emit_event(self, phase: str, action: str, **payload: Any) -> None:
        event = {"phase": phase, "action": action, **payload}
        self.event_log.append(event)

    def _input_signature(self, study: MechanismStudy) -> str:
        stable_states = [
            {
                "state_id": state.state_id,
                "role": state.role,
                "charge": state.charge,
                "multiplicity": state.multiplicity,
                "identity_fingerprint": state.identity_fingerprint,
                "canonical_geometry": state.canonical_geometry.to_dict(),
                "metadata": state.metadata,
            }
            for state in study.stable_states
        ]
        payload = {
            "study_id": study.study_id,
            "routes": [route.to_dict() for route in study.routes],
            "stable_states": stable_states,
            "atom_identity_map": (
                study.atom_identity_map.to_dict() if study.atom_identity_map is not None else None
            ),
        }
        return _fingerprint(payload)

    def _phase_signature(self, phase: str, payload: dict[str, Any]) -> str:
        return _fingerprint({"phase": phase, **payload})

    def _run_phase_s0(self) -> None:
        signature = self._phase_signature(
            "S0",
            {
                "input_signature": self.study.metadata.get("input_signature"),
                "routes": [route.to_dict() for route in self.study.routes],
            },
        )
        if self.study.phase_fingerprints.get("S0") == signature:
            return
        self._emit_event("S0", "phase_started")
        if not self.study.stable_states:
            raise ValueError("MechanismStudy requires at least one stable state")
        if not self.study.routes:
            raise ValueError("MechanismStudy requires at least one route")
        reactant = next(
            (state for state in self.study.stable_states if state.role == "reactant"),
            None,
        )
        product = next(
            (state for state in self.study.stable_states if state.role == "product"),
            None,
        )
        if reactant is not None:
            self.study.reactant_id = reactant.state_id
        if product is not None:
            self.study.product_id = product.state_id
        self._ensure_route_targets()
        self._seed_frontier_if_needed()
        self.study.phase_fingerprints["S0"] = signature
        self.study.status = "running"
        self._persist_study_bundle()
        self._emit_event("S0", "phase_completed")

    def _run_phase_s1_initial(self) -> None:
        signature = self._phase_signature(
            "S1",
            {
                "stable_state_ids": [state.state_id for state in self.study.stable_states],
                "input_signature": self.study.metadata.get("input_signature"),
            },
        )
        if self.study.phase_fingerprints.get("S1") == signature and all(
            state.ensemble is not None for state in self.study.stable_states
        ):
            return
        self._emit_event("S1", "phase_started")
        for index, state in enumerate(list(self.study.stable_states)):
            normalized = self._normalize_state_ensemble(state, state_index=index)
            self.study.stable_states[index] = normalized
        self.study.phase_fingerprints["S1"] = signature
        self._sync_network(self.study)
        self._persist_study_bundle()
        self._emit_event("S1", "phase_completed", n_states=len(self.study.stable_states))

    def _run_phase_s4(self) -> None:
        signature = self._phase_signature(
            "S4",
            {"high_fidelity_profile": str(self.high_fidelity_profile)},
        )
        if self.study.phase_fingerprints.get("S4") == signature:
            return
        self._emit_event("S4", "phase_started")
        if self.thermochemistry_provider is not None:
            self.study.metadata["high_fidelity"] = {
                "profile": str(self.high_fidelity_profile),
                "thermochemistry_provider": type(self.thermochemistry_provider).__name__,
            }
        else:
            self.study.metadata.setdefault("high_fidelity", None)
        self.study.phase_fingerprints["S4"] = signature
        self._persist_study_bundle()

    def _run_frontier_loop(self) -> None:
        self._emit_event(
            "SR",
            "frontier_loop_started",
            frontier_size=len(self.study.frontier.queue),
        )
        while (
            not self.study.frontier.empty()
            and len(self.study.elementary_steps) < self.max_elementary_steps
        ):
            source_state_id, route_id = self.study.frontier.pop()
            depth = self.study.frontier.depth_for(source_state_id, route_id)
            if depth > self.study.frontier.max_depth:
                self._emit_event(
                    "SR",
                    "frontier_pruned",
                    source_state_id=source_state_id,
                    route_id=route_id,
                    depth=depth,
                )
                continue
            source_state = self._require_state(source_state_id)
            source_state.metadata.setdefault("route_id", route_id)
            route = self._require_route(route_id)
            target_state = self.study.get_state(route.product_id) if route.product_id else None
            route_fingerprint = self._route_fingerprint(source_state, route, target_state, depth)
            exploration_key = self._exploration_key(source_state_id, route_id)
            if self._route_status_matches(
                exploration_key,
                route_fingerprint,
                {"completed", "stopped"},
            ):
                continue

            self._emit_event(
                "S2",
                "path_search_started",
                source_state_id=source_state_id,
                route_id=route_id,
                depth=depth,
            )
            path_result = self.path_strategy.search(
                source_state,
                target_state,
                route.coordinate_plan,
                self.ensemble_profile,
            )
            self.study.metadata.setdefault("path_results", {})[exploration_key] = (
                path_result.to_dict()
            )
            self._merge_gate_policies(path_result)
            self._persist_route_manifest(
                source_state_id=source_state_id,
                route=route,
                route_fingerprint=route_fingerprint,
                status="searched",
                path_result=path_result,
                depth=depth,
            )
            self._emit_event(
                "S2",
                "path_search_completed",
                source_state_id=source_state_id,
                route_id=route_id,
                n_points=len(path_result.points),
            )

            refinement_requests = self._build_refinement_requests(source_state, route, path_result)
            self._emit_event(
                "S3",
                "refinement_started",
                source_state_id=source_state_id,
                route_id=route_id,
                n_requests=len(refinement_requests),
            )
            manifest = self.refinement_provider.refine(
                refinement_requests,
                self.low_fidelity_profile,
            )
            self.study.metadata.setdefault("refinement_manifests", {})[manifest.manifest_id] = (
                manifest.to_dict()
            )
            self._persist_refinement_manifest(manifest)
            canonical_ts = manifest.canonical_winner
            if canonical_ts is None:
                self._mark_route_status(exploration_key, route_fingerprint, status="failed")
                self._persist_route_manifest(
                    source_state_id=source_state_id,
                    route=route,
                    route_fingerprint=route_fingerprint,
                    status="failed",
                    path_result=path_result,
                    refinement_manifest=manifest,
                    depth=depth,
                )
                continue
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
                source_state_id=source_state_id,
                route_id=route_id,
                ts_id=canonical_ts.point_id,
            )
            irc_result = self.endpoint_provider.run_irc(canonical_ts, self.low_fidelity_profile)
            endpoint_match = self.endpoint_provider.classify_endpoints(
                irc_result,
                self.study.stable_states,
            )
            self.study.metadata.setdefault("irc_results", {})[irc_result.irc_id] = (
                irc_result.to_dict()
            )
            self.study.metadata.setdefault("endpoint_matches", {})[exploration_key] = (
                endpoint_match.to_dict()
            )

            if self._needs_review(
                endpoint_match,
                source_state_id=source_state_id,
                route_id=route_id,
                depth=depth,
            ):
                self._create_decision(
                    source_state=source_state,
                    route=route,
                    depth=depth,
                    route_fingerprint=route_fingerprint,
                    path_result=path_result,
                    refinement_manifest=manifest,
                    irc_result=irc_result,
                    endpoint_match=endpoint_match,
                )
                self.study.status = "waiting"
                self._persist_study_bundle()
                run_gates(self.study)
                self._persist_study_bundle()
                return

            self._apply_endpoint_match(
                source_state=source_state,
                route=route,
                depth=depth,
                route_fingerprint=route_fingerprint,
                canonical_ts=canonical_ts,
                irc_result=irc_result,
                endpoint_match=endpoint_match,
            )
            self._persist_route_manifest(
                source_state_id=source_state_id,
                route=route,
                route_fingerprint=route_fingerprint,
                status="completed",
                path_result=path_result,
                refinement_manifest=manifest,
                irc_result=irc_result,
                endpoint_match=endpoint_match,
                depth=depth,
            )
            self._emit_event(
                "SR",
                "irc_completed",
                source_state_id=source_state_id,
                route_id=route_id,
                verdict=endpoint_match.verdict,
            )
            self._persist_study_bundle()

        if len(self.study.elementary_steps) >= self.max_elementary_steps:
            self.study.metadata["frontier_cap_reached"] = True
            self._emit_event(
                "SR",
                "max_elementary_steps_reached",
                max_steps=self.max_elementary_steps,
            )

    def _normalize_state_ensemble(
        self,
        state: StableState,
        *,
        state_index: int | None = None,
    ) -> StableState:
        state_dir = self.study_dir / "states" / state.state_id
        manifest_path = state_dir / "ensemble_manifest.json"
        state_fingerprint = self._state_fingerprint(state)
        if state.ensemble is not None and manifest_path.exists():
            manifest = _read_json(manifest_path)
            if str(manifest.get("fingerprint") or "") == state_fingerprint:
                return state
        if state.ensemble is None:
            state = replace(
                state,
                ensemble=self.ensemble_provider.generate(state, self.ensemble_profile),
            )
        payload = {
            "fingerprint": state_fingerprint,
            "state": state.to_dict(),
        }
        _write_json_atomic(manifest_path, payload)
        self._sync_network(self.study)
        if state_index is None:
            existing = self.study.get_state(state.state_id)
            if existing is None:
                self.study.stable_states.append(state)
            else:
                for idx, current in enumerate(self.study.stable_states):
                    if current.state_id == state.state_id:
                        self.study.stable_states[idx] = state
                        break
        return state

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
        return ArtifactRef(path=str(seed_path), sha256=_fingerprint(payload), kind="path_point")

    def _request_provenance(
        self,
        path_result: PathResult,
        source_state: StableState,
        route: MechanismRoute,
    ) -> Provenance:
        if path_result.points and path_result.points[0].provenance is not None:
            return path_result.points[0].provenance
        return Provenance(
            provider="study-orchestrator",
            provider_version="1.0",
            provider_commit="m0",
            strategy=path_result.strategy_id or path_result.strategy,
            strategy_version=path_result.strategy_version or "1.0",
            profile_id=str(self.low_fidelity_profile),
            schema_version="m0",
            input_signature=_fingerprint(
                {
                    "source_state_id": source_state.state_id,
                    "route": route.to_dict(),
                    "path_strategy": path_result.strategy,
                }
            ),
        )

    def _apply_endpoint_match(
        self,
        *,
        source_state: StableState,
        route: MechanismRoute,
        depth: int,
        route_fingerprint: str,
        canonical_ts: StationaryPoint,
        irc_result: IrcResult,
        endpoint_match: EndpointMatchResult,
    ) -> None:
        if endpoint_match.verdict == "FAILED":
            self._mark_route_status(
                self._exploration_key(source_state.state_id, route.route_id),
                route_fingerprint,
                status="failed",
            )
            return
        sink_state: StableState | None = None
        if endpoint_match.verdict == "MATCH_EXISTING":
            if endpoint_match.state_id is None:
                raise ValueError("MATCH_EXISTING endpoint result requires a state_id")
            sink_state = self._require_state(endpoint_match.state_id)
        elif endpoint_match.verdict == "NEW_STATE":
            sink_state = self._register_new_state(endpoint_match, route, depth)
        else:
            raise ValueError(f"Unexpected endpoint verdict in apply: {endpoint_match.verdict}")

        self._add_elementary_step(
            source_state=source_state,
            sink_state=sink_state,
            route=route,
            canonical_ts=canonical_ts,
            irc_result=irc_result,
        )
        self._mark_route_status(
            self._exploration_key(source_state.state_id, route.route_id),
            route_fingerprint,
            status="completed",
        )

    def _register_new_state(
        self,
        endpoint_match: EndpointMatchResult,
        route: MechanismRoute,
        depth: int,
    ) -> StableState:
        candidate_payload = endpoint_match.evidence.get("candidate_state")
        if not isinstance(candidate_payload, dict):
            raise ValueError("NEW_STATE endpoint result missing candidate_state payload")
        state = StableState.from_dict(candidate_payload)
        existing = self.study.get_state(state.state_id)
        if existing is not None:
            return existing
        self._upsert_minimum_stationary_point(endpoint_match, state.state_id)
        normalized = self._normalize_state_ensemble(state)
        self._sync_network(self.study)
        next_depth = depth + 1
        if (
            normalized.state_id != self.study.product_id
            and next_depth <= self.study.frontier.max_depth
            and len(self.study.elementary_steps) < self.max_elementary_steps
        ):
            derived_route = self._derive_route(route, normalized)
            if self._find_route(derived_route.route_id) is None:
                self.study.routes.append(derived_route)
            self.study.frontier.push(normalized.state_id, derived_route.route_id, depth=next_depth)
            self._emit_event(
                "SR",
                "frontier_enqueued",
                source_state_id=normalized.state_id,
                route_id=derived_route.route_id,
                depth=next_depth,
            )
        return normalized

    def _add_elementary_step(
        self,
        *,
        source_state: StableState,
        sink_state: StableState,
        route: MechanismRoute,
        canonical_ts: StationaryPoint,
        irc_result: IrcResult,
    ) -> None:
        step_index = len(self.study.elementary_steps) + 1
        step_id = f"step_{step_index:03d}"
        source_energy = self._state_reference_energy(source_state)
        sink_energy = self._state_reference_energy(sink_state)
        ts_energy = canonical_ts.energy_hartree
        edge = ElementaryStepEdge(
            step_id=step_id,
            source_state_id=source_state.state_id,
            sink_state_id=sink_state.state_id,
            ts_id=canonical_ts.point_id,
            path_strategy=route.path_strategy,
            coordinate_plan=route.coordinate_plan,
            irc_connectivity={
                "irc_id": irc_result.irc_id,
                "success": irc_result.success,
                "complete": irc_result.complete,
            },
            barrier_forward=(
                ts_energy - source_energy
                if ts_energy is not None and source_energy is not None
                else None
            ),
            barrier_reverse=(
                ts_energy - sink_energy
                if ts_energy is not None and sink_energy is not None
                else None
            ),
            fidelity=route.fidelity,
            status="confirmed",
        )
        self.study.elementary_steps.append(edge)
        self._sync_network(self.study)

    def _state_reference_energy(self, state: StableState) -> float | None:
        if state.ensemble is None:
            return None
        minimum = state.ensemble.global_minimum()
        return None if minimum is None else minimum.energy_hartree

    def _create_decision(
        self,
        *,
        source_state: StableState,
        route: MechanismRoute,
        depth: int,
        route_fingerprint: str,
        path_result: PathResult,
        refinement_manifest: RefinementManifest,
        irc_result: IrcResult,
        endpoint_match: EndpointMatchResult,
    ) -> DecisionPoint:
        decision_id = f"decision_{len(self.study.decision_points) + 1:03d}"
        payload = {
            "source_state_id": source_state.state_id,
            "route_id": route.route_id,
            "depth": depth,
            "endpoint_match": endpoint_match.to_dict(),
            "irc_result": irc_result.to_dict(),
        }
        decision = DecisionPoint(
            id=decision_id,
            type="mechanism_frontier_review",
            status="waiting",
            options=["continue", "promote_to_s4", "stop_branch", "edit_route"],
            payload=payload,
            created_at=_utc_now(),
        )
        self.study.decision_points.append(decision)
        self.study.metadata.setdefault("pending_decisions", {})[decision.id] = {
            "source_state_id": source_state.state_id,
            "route_id": route.route_id,
            "depth": depth,
            "route_fingerprint": route_fingerprint,
            "exploration_key": self._exploration_key(source_state.state_id, route.route_id),
            "path_result": path_result.to_dict(),
            "refinement_manifest": refinement_manifest.to_dict(),
            "irc_result": irc_result.to_dict(),
            "endpoint_match": endpoint_match.to_dict(),
        }
        self._persist_decision(decision)
        self._emit_event(
            "SR",
            "decision_required",
            decision_id=decision.id,
            source_state_id=source_state.state_id,
            route_id=route.route_id,
        )
        return decision

    def _parse_resolution(self, resolution: Any) -> tuple[str, dict[str, Any]]:
        if isinstance(resolution, str):
            return resolution, {}
        if isinstance(resolution, dict):
            action = str(resolution.get("resolution") or resolution.get("action") or "continue")
            return action, dict(resolution)
        return "continue", {}

    def _needs_review(self, endpoint_match: EndpointMatchResult, **context: Any) -> bool:
        if endpoint_match.verdict == "AMBIGUOUS":
            return True
        if self.require_review is None:
            return False
        return bool(self.require_review(endpoint_match, self.study, context))

    def _derive_route(self, route: MechanismRoute, source_state: StableState) -> MechanismRoute:
        target_id = route.product_id or self.study.product_id
        if target_id == source_state.state_id:
            target_id = self.study.product_id
        return MechanismRoute(
            route_id=f"{route.route_id}__{source_state.state_id}",
            coordinate_plan=route.coordinate_plan,
            path_strategy=route.path_strategy,
            fidelity=route.fidelity,
            reactant_id=source_state.state_id,
            product_id=target_id,
            ts_guess_id=route.ts_guess_id,
            label=route.label or f"{source_state.state_id} → {target_id or 'unknown'}",
        )

    def _route_fingerprint(
        self,
        source_state: StableState,
        route: MechanismRoute,
        target_state: StableState | None,
        depth: int,
    ) -> str:
        return _fingerprint(
            {
                "source_state_id": source_state.state_id,
                "target_state_id": target_state.state_id if target_state is not None else None,
                "route": route.to_dict(),
                "depth": depth,
                "input_signature": self.study.metadata.get("input_signature"),
            }
        )

    def _state_fingerprint(self, state: StableState) -> str:
        return _fingerprint(
            {
                "state_id": state.state_id,
                "role": state.role,
                "canonical_geometry": state.canonical_geometry.to_dict(),
                "charge": state.charge,
                "multiplicity": state.multiplicity,
                "identity_fingerprint": state.identity_fingerprint,
                "metadata": state.metadata,
            }
        )

    def _persist_route_manifest(
        self,
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
        route_dir = self.study_dir / "routes" / f"{source_state_id}__{route.route_id}"
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

    def _persist_refinement_manifest(self, manifest: RefinementManifest) -> None:
        ref_dir = self.study_dir / "refinements" / manifest.manifest_id
        _write_json_atomic(ref_dir / "refinement_manifest.json", manifest.to_dict())

    def _persist_decision(self, decision: DecisionPoint) -> None:
        decision_path = self.study_dir / "decisions" / f"{decision.id}.json"
        _write_json_atomic(decision_path, decision.to_dict())

    def _sync_network(self, study: MechanismStudy) -> None:
        nodes = {state.state_id: state.to_node() for state in study.stable_states}
        study.network = ReactionNetwork(nodes=nodes, edges=list(study.elementary_steps))

    def _seed_frontier_if_needed(self) -> None:
        if not self.study.frontier.empty():
            return
        route_statuses = dict(self.study.metadata.get("route_statuses") or {})
        if route_statuses:
            return
        reactant_id = self.study.reactant_id
        if reactant_id is None and self.study.stable_states:
            reactant = next(
                (state for state in self.study.stable_states if state.role == "reactant"),
                None,
            )
            reactant_id = (
                reactant.state_id if reactant is not None else self.study.stable_states[0].state_id
            )
        if reactant_id is None:
            return
        for route in self.study.routes:
            if route.reactant_id is None:
                route.reactant_id = reactant_id
            self.study.frontier.push(reactant_id, route.route_id, depth=0)

    def _ensure_route_targets(self) -> None:
        for route in self.study.routes:
            if route.reactant_id is None:
                route.reactant_id = self.study.reactant_id
            if route.product_id is None:
                route.product_id = self.study.product_id

    def _exploration_key(self, source_state_id: str, route_id: str) -> str:
        return f"{source_state_id}::{route_id}"

    def _route_status_matches(
        self,
        exploration_key: str,
        route_fingerprint: str,
        statuses: set[str],
    ) -> bool:
        route_statuses = dict(self.study.metadata.get("route_statuses") or {})
        record = route_statuses.get(exploration_key)
        if not isinstance(record, dict):
            return False
        return (
            str(record.get("fingerprint") or "") == route_fingerprint
            and str(record.get("status") or "") in statuses
        )

    def _mark_route_status(
        self,
        exploration_key: str,
        route_fingerprint: str,
        *,
        status: str,
    ) -> None:
        route_statuses = dict(self.study.metadata.get("route_statuses") or {})
        route_statuses[exploration_key] = {
            "fingerprint": route_fingerprint,
            "status": status,
            "updated_at": _utc_now(),
        }
        self.study.metadata["route_statuses"] = route_statuses

    def _require_state(self, state_id: str) -> StableState:
        state = self.study.get_state(state_id)
        if state is None:
            raise KeyError(f"Unknown stable state {state_id!r}")
        return state

    def _find_route(self, route_id: str) -> MechanismRoute | None:
        for route in self.study.routes:
            if route.route_id == route_id:
                return route
        return None

    def _require_route(self, route_id: str) -> MechanismRoute:
        route = self._find_route(route_id)
        if route is None:
            raise KeyError(f"Unknown route {route_id!r}")
        return route

    def _replace_route(self, updated_route: MechanismRoute) -> None:
        for index, route in enumerate(self.study.routes):
            if route.route_id == updated_route.route_id:
                self.study.routes[index] = updated_route
                return
        self.study.routes.append(updated_route)

    def _updated_route(
        self,
        route: MechanismRoute,
        resolution_payload: dict[str, Any],
    ) -> MechanismRoute:
        route_dict = route.to_dict()
        for key in (
            "path_strategy",
            "fidelity",
            "label",
            "reactant_id",
            "product_id",
            "ts_guess_id",
        ):
            if key in resolution_payload:
                route_dict[key] = resolution_payload[key]
        if "coordinate_plan" in resolution_payload and isinstance(
            resolution_payload["coordinate_plan"], dict
        ):
            route_dict["coordinate_plan"] = resolution_payload["coordinate_plan"]
        return MechanismRoute.from_dict(route_dict)

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

    def _upsert_minimum_stationary_point(
        self,
        endpoint_match: EndpointMatchResult,
        state_id: str,
    ) -> None:
        point_data = endpoint_match.evidence.get("minimum_stationary_point")
        if not isinstance(point_data, dict):
            return
        point = StationaryPoint.from_dict(dict(point_data))
        if point.state_id != state_id:
            point.state_id = state_id
        self._upsert_stationary_point(point)

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


def replace_seed_candidate(
    candidate_id: str,
    artifact: ArtifactRef,
    point: PathPoint,
) -> SeedCandidate:
    """Build a minimal SeedCandidate-like object without importing helpers."""
    return SeedCandidate(
        id=candidate_id,
        kind="ts_seed",
        geometry=artifact,
        rank=1,
        selection_mode="path_candidate_fallback",
        confidence="medium",
        evidence={"point_id": point.point_id, "progress": point.progress},
    )


__all__ = ["StudyOrchestrator"]
