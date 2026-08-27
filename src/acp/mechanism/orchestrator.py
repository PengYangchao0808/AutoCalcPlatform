# pyright: reportMissingImports=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportExplicitAny=false, reportUnusedCallResult=false, reportUnnecessaryComparison=false
"""Study orchestrator for contract-first mechanism exploration."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import numpy as np

from acp.calculations.irc.contracts import EndpointMatchResult, EndpointProvider, IrcResult
from acp.core.state import EventLog
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan

from ._helpers import write_json_atomic as _write_json_atomic
from .endpoint import DefaultEndpointProvider, EndpointMatchThresholds
from .engines.elementary_step import (
    ElementaryStepEngine,
    exploration_key,
    mark_route_status,
    persist_refinement_manifest,
    persist_route_manifest,
)
from .gates import run_gates
from .layout import resolve_study_layout
from .models import (
    ArtifactRef,
    DecisionPoint,
    ElementaryStepEdge,
    ExplorationFrontier,
    MechanismRevision,
    MechanismRoute,
    MechanismStudy,
    PathResult,
    Provenance,
    ReactionNetwork,
    SelectedBond,
    StableState,
    StationaryPoint,
    StationaryPointRequest,
    StudyCycle,
)
from .providers.contracts import (
    EnsembleProvider,
    PathSearchStrategy,
    RefinementManifest,
    RefinementProvider,
    ThermochemistryProvider,
)
from .providers.thermo import resolve_standard_state
from .reports import PromotionPolicy, select_s4_candidates

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
        require_sr_review: bool = False,
        endpoint_match_thresholds: EndpointMatchThresholds | None = None,
        validate_endpoint_minimum: bool = True,
    ) -> None:
        self.study_template = MechanismStudy.from_dict(study.to_dict())
        self.study_root = Path(study_root)
        self.layout = resolve_study_layout(self.study_root, study.study_id)
        self.study_dir = self.layout.analysis_root
        self.ensemble_provider = ensemble_provider
        self.path_strategy = path_strategy
        self.refinement_provider = refinement_provider
        self.endpoint_provider = endpoint_provider or DefaultEndpointProvider(
            backend=endpoint_backend,
            thresholds=endpoint_match_thresholds,
            validate_minimum=validate_endpoint_minimum,
            work_root=self.layout.endpoint_root,
        )
        self.thermochemistry_provider = thermochemistry_provider
        self.ensemble_profile = ensemble_profile
        self.low_fidelity_profile = low_fidelity_profile
        self.high_fidelity_profile = high_fidelity_profile
        self.max_elementary_steps = max_elementary_steps
        self.require_review = require_review
        self.require_sr_review = require_sr_review
        self.event_log = EventLog(self.layout.study_events)
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
        self._write_v2_result_layer()
        return self.study

    def resume(self, decision_resolutions: dict[str, Any]) -> MechanismStudy:
        """Resolve persisted DecisionPoints and continue the frontier loop."""
        self.study = self._load_or_initialize_study()
        pending = dict(self.study.metadata.get("pending_decisions") or {})
        if not pending:
            return self.study

        waiting_ids = {d.id for d in self.study.decision_points if d.status == "waiting"}
        stale_ids = sorted(set(decision_resolutions) - waiting_ids)
        if stale_ids:
            logger.warning("Ignoring resolutions for non-waiting decisions: %s", stale_ids)

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
            if action == "sr_revision":
                self._apply_sr_revision(decision, context, pending, resolution_payload)
                continue
            if action == "stop_branch":
                decision.status = "resolved"
                decision.resolution = "stop_branch"
                decision.resolved_at = _utc_now()
                mark_route_status(
                    self.study,
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
                    raw_evidence = match_dict.get("evidence")
                    evidence = (
                        {str(key): value for key, value in raw_evidence.items()}
                        if isinstance(raw_evidence, dict)
                        else {}
                    )
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
        self._write_v2_result_layer()
        return self.study

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

    def _write_v2_result_layer(self) -> None:
        """Project a v2 ``RESULT/`` layer from the study tree (non-fatal)."""
        if self.study.status != "completed":
            return
        try:
            from acp.mechanism.results_v2 import write_v2_result_layer

            write_v2_result_layer(self.study_root)
        except Exception:
            logger.exception("v2 RESULT layer projection failed (non-fatal)")

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
        """Run promoted S4 high-fidelity confirmation for selected TS candidates.

        The phase always attempts high-fidelity refinement to recover electronic
        single-point energies for promoted TS candidates. When
        ``thermochemistry_provider`` is available and a frequency artifact can be
        located, the refined points are additionally enriched with a
        thermochemistry payload; otherwise S4 still completes as SP-only.
        """

        promotion_policy = self._promotion_policy()
        candidate_ids = select_s4_candidates(self.study, promotion_policy)
        signature_candidate_ids = sorted(candidate_ids)
        signature = self._phase_signature(
            "S4",
            {
                "promotion_policy": promotion_policy,
                "candidate_ids": signature_candidate_ids,
                "high_fidelity_profile": self._profile_payload(self.high_fidelity_profile),
            },
        )
        if self.study.phase_fingerprints.get("S4") == signature:
            if self.study.metadata.get("high_fidelity") is None:
                self.study.metadata.setdefault("high_fidelity", None)
                self.study.quality = "medium"
            return
        self._emit_event(
            "S4",
            "phase_started",
            n_candidates=len(candidate_ids),
            promotion_policy=promotion_policy,
        )
        if not candidate_ids:
            self.study.metadata["high_fidelity"] = None
            self.study.quality = "medium"
            self.study.phase_fingerprints["S4"] = signature
            self._persist_study_bundle()
            return

        requests, request_failures = self._build_s4_refinement_requests(candidate_ids)
        manifest_ids: list[str] = []
        succeeded_candidates: set[str] = set()
        failed_candidates = {failure["candidate_id"] for failure in request_failures}
        failures = list(request_failures)
        thermochemistry_failures: list[dict[str, Any]] = []
        batch_errors: list[str] = []

        if requests:
            manifests, batch_errors = self._run_high_fidelity_refinements(requests)
            for manifest in manifests:
                manifest_ids.append(manifest.manifest_id)
                self.study.metadata.setdefault("refinement_manifests", {})[manifest.manifest_id] = (
                    manifest.to_dict()
                )
                persist_refinement_manifest(self.layout, manifest)
                (
                    manifest_successes,
                    manifest_failures,
                    thermo_errors,
                ) = self._apply_s4_manifest(manifest)
                succeeded_candidates.update(manifest_successes)
                failed_candidates.update(failure["candidate_id"] for failure in manifest_failures)
                failures.extend(manifest_failures)
                thermochemistry_failures.extend(thermo_errors)

        unresolved_candidates = set(candidate_ids) - succeeded_candidates - failed_candidates
        for candidate_id in sorted(unresolved_candidates):
            failures.append(
                {
                    "candidate_id": candidate_id,
                    "error": "No successful high-fidelity refinement result was produced",
                }
            )
        failed_candidates.update(unresolved_candidates)
        self.study.quality = "high" if succeeded_candidates == set(candidate_ids) else "medium"
        self.study.metadata["high_fidelity"] = {
            "profile": self._profile_name(self.high_fidelity_profile),
            "promotion_policy": promotion_policy,
            "candidate_ids": candidate_ids,
            "manifest_ids": manifest_ids,
            "successful_candidate_ids": sorted(succeeded_candidates),
            "failed_candidate_ids": sorted(failed_candidates),
            "failures": failures,
            "batch_errors": batch_errors,
            "thermochemistry_provider": (
                type(self.thermochemistry_provider).__name__
                if self.thermochemistry_provider is not None
                else None
            ),
            "thermochemistry_failures": thermochemistry_failures,
        }
        self.study.phase_fingerprints["S4"] = signature
        self._sync_network(self.study)
        self._persist_study_bundle()

    def _promotion_policy(self) -> PromotionPolicy:
        runner_meta = self.study.metadata.get("study_runner")
        if isinstance(runner_meta, dict):
            policy = runner_meta.get("promotion_policy")
            if isinstance(policy, str):
                normalized = policy.strip()
                if normalized in {"all_confirmed", "rate_relevant", "user_selected"}:
                    return cast(PromotionPolicy, normalized)
        return "all_confirmed"

    def _profile_name(self, profile: Any) -> str:
        profile_name = getattr(profile, "name", None)
        if isinstance(profile_name, str) and profile_name:
            return profile_name
        return str(profile)

    def _profile_payload(self, profile: Any) -> Any:
        if is_dataclass(profile) and not isinstance(profile, type):
            return asdict(cast(Any, profile))
        if isinstance(profile, dict):
            return dict(profile)
        if hasattr(profile, "to_dict"):
            to_dict = getattr(profile, "to_dict")
            if callable(to_dict):
                return to_dict()
        return str(profile)

    def _build_s4_refinement_requests(
        self,
        candidate_ids: list[str],
    ) -> tuple[list[StationaryPointRequest], list[dict[str, str]]]:
        requests: list[StationaryPointRequest] = []
        failures: list[dict[str, str]] = []
        candidate_map = {point.point_id: point for point in self.study.stationary_points}
        for candidate_id in candidate_ids:
            point = candidate_map.get(candidate_id)
            if point is None:
                failures.append(
                    {"candidate_id": candidate_id, "error": "Selected S4 candidate is missing"}
                )
                continue
            primary_edge = next(
                (edge for edge in self.study.elementary_steps if edge.ts_id == candidate_id),
                None,
            )
            coordinate_plan = None
            if point.route_id is not None:
                route = self._find_route(point.route_id)
                if route is not None:
                    coordinate_plan = route.coordinate_plan
            if coordinate_plan is None and primary_edge is not None:
                coordinate_plan = primary_edge.coordinate_plan
            parent_state_id = point.state_id or (
                primary_edge.source_state_id if primary_edge is not None else None
            )
            parent_state = (
                self.study.get_state(parent_state_id) if parent_state_id is not None else None
            )
            requests.append(
                StationaryPointRequest(
                    id=candidate_id,
                    role=point.role,
                    kind=point.kind,
                    input_geometry=point.geometry,
                    coordinate_plan=coordinate_plan,
                    fallback_geometries=list(point.artifacts),
                    source_stage="S4",
                    charge=(
                        point.charge
                        if point.charge is not None
                        else (parent_state.charge if parent_state else 0)
                    ),
                    multiplicity=(
                        point.multiplicity
                        if point.multiplicity is not None
                        else (parent_state.multiplicity if parent_state else 1)
                    ),
                    atom_mapping=self.study.atom_identity_map,
                    parent_state_id=parent_state_id,
                    route_id=point.route_id,
                    ensemble_correction=None,
                    provenance=self._s4_request_provenance(point, parent_state_id),
                )
            )
        return requests, failures

    def _s4_request_provenance(
        self,
        point: StationaryPoint,
        parent_state_id: str | None,
    ) -> Provenance:
        base = point.provenance
        return Provenance(
            provider="study-orchestrator",
            provider_version="1.0",
            provider_commit="m0",
            strategy=(base.strategy if base is not None else "s4-promotion"),
            strategy_version=(base.strategy_version if base is not None else "1.0"),
            profile_id=self._profile_name(self.high_fidelity_profile),
            schema_version=(base.schema_version if base is not None else "m0"),
            input_signature=_fingerprint(
                {
                    "study_id": self.study.study_id,
                    "candidate_id": point.point_id,
                    "parent_state_id": parent_state_id,
                    "route_id": point.route_id,
                    "profile": self._profile_payload(self.high_fidelity_profile),
                }
            ),
        )

    def _run_high_fidelity_refinements(
        self,
        requests: list[StationaryPointRequest],
    ) -> tuple[list[RefinementManifest], list[str]]:
        try:
            return [self.refinement_provider.refine(requests, self.high_fidelity_profile)], []
        except Exception as exc:
            logger.warning(
                "S4 batch refinement failed; falling back to per-candidate runs: %s",
                exc,
            )
            manifests: list[RefinementManifest] = []
            failures = [str(exc)]
            for request in requests:
                try:
                    manifests.append(
                        self.refinement_provider.refine([request], self.high_fidelity_profile)
                    )
                except Exception as request_exc:
                    logger.warning("S4 refinement failed for %s: %s", request.id, request_exc)
                    failures.append(f"{request.id}: {request_exc}")
            return manifests, failures

    def _apply_s4_manifest(
        self,
        manifest: RefinementManifest,
    ) -> tuple[set[str], list[dict[str, str]], list[dict[str, str]]]:
        successes: set[str] = set()
        failures: list[dict[str, str]] = []
        thermochemistry_failures: list[dict[str, str]] = []
        for attempt in manifest.attempts:
            candidate_id = attempt.request_id
            if attempt.status != "success" or attempt.stationary_point is None:
                failures.append(
                    {
                        "candidate_id": candidate_id,
                        "error": str(attempt.evidence.get("error") or "refinement_failed"),
                    }
                )
                continue
            self._merge_high_fidelity_point(
                candidate_id,
                attempt.stationary_point,
                manifest.manifest_id,
            )
            thermo_failure = self._enrich_high_fidelity_thermochemistry(candidate_id)
            if thermo_failure is not None:
                thermochemistry_failures.append(thermo_failure)
            successes.add(candidate_id)
        if not manifest.attempts and manifest.canonical_winner is not None:
            candidate_id = manifest.canonical_winner.point_id
            self._merge_high_fidelity_point(
                candidate_id,
                manifest.canonical_winner,
                manifest.manifest_id,
            )
            thermo_failure = self._enrich_high_fidelity_thermochemistry(candidate_id)
            if thermo_failure is not None:
                thermochemistry_failures.append(thermo_failure)
            successes.add(candidate_id)
        return successes, failures, thermochemistry_failures

    def _merge_high_fidelity_point(
        self,
        candidate_id: str,
        refined_point: StationaryPoint,
        manifest_id: str,
    ) -> None:
        existing = next(
            (point for point in self.study.stationary_points if point.point_id == candidate_id),
            None,
        )
        low_fidelity = self._profile_name(self.low_fidelity_profile)
        high_fidelity = self._profile_name(self.high_fidelity_profile)
        metadata = dict(existing.metadata) if existing is not None else {}
        metadata.update(refined_point.metadata)
        energies = dict(metadata.get("energies_hartree") or {})
        if existing is not None and existing.energy_hartree is not None:
            existing_fidelity = str(existing.metadata.get("fidelity") or low_fidelity)
            energies.setdefault(existing_fidelity, existing.energy_hartree)
        if refined_point.energy_hartree is not None:
            energies[high_fidelity] = refined_point.energy_hartree
        if energies:
            metadata["energies_hartree"] = energies
        thermo_history = dict(metadata.get("thermochemistry_by_fidelity") or {})
        if existing is not None and "thermochemistry" in existing.metadata:
            existing_fidelity = str(existing.metadata.get("fidelity") or low_fidelity)
            thermo_history.setdefault(existing_fidelity, existing.metadata.get("thermochemistry"))
        if "thermochemistry" in refined_point.metadata:
            thermo_history[high_fidelity] = refined_point.metadata.get("thermochemistry")
        if thermo_history:
            metadata["thermochemistry_by_fidelity"] = thermo_history
        metadata["fidelity"] = high_fidelity
        metadata["confirmed"] = True
        metadata["canonical"] = True
        metadata["s4_manifest_id"] = manifest_id
        if refined_point.point_id != candidate_id:
            metadata["s4_refined_point_id"] = refined_point.point_id
        merged = replace(
            refined_point,
            point_id=candidate_id,
            role=(existing.role if existing is not None else refined_point.role),
            kind=(existing.kind if existing is not None else refined_point.kind),
            state_id=(
                existing.state_id
                if existing is not None and existing.state_id
                else refined_point.state_id
            ),
            route_id=(
                existing.route_id
                if existing is not None and existing.route_id
                else refined_point.route_id
            ),
            artifacts=self._merge_artifacts(existing, refined_point),
            metadata=metadata,
        )
        self._upsert_stationary_point(merged)
        self._refresh_high_fidelity_barriers(candidate_id, high_fidelity)

    def _merge_artifacts(
        self,
        existing: StationaryPoint | None,
        refined_point: StationaryPoint,
    ) -> list[ArtifactRef]:
        merged: list[ArtifactRef] = []
        artifacts = (existing.artifacts if existing is not None else []) + list(
            refined_point.artifacts
        )
        for artifact in artifacts:
            signature = (artifact.path, artifact.sha256, artifact.kind)
            if any((item.path, item.sha256, item.kind) == signature for item in merged):
                continue
            merged.append(artifact)
        return merged

    def _refresh_high_fidelity_barriers(self, candidate_id: str, fidelity: str) -> None:
        point = next(
            (
                current
                for current in self.study.stationary_points
                if current.point_id == candidate_id
            ),
            None,
        )
        if point is None:
            return
        for edge in self.study.elementary_steps:
            if edge.ts_id != candidate_id:
                continue
            source_state = self.study.get_state(edge.source_state_id)
            sink_state = self.study.get_state(edge.sink_state_id)
            source_energy = (
                self._state_reference_energy(source_state) if source_state is not None else None
            )
            sink_energy = (
                self._state_reference_energy(sink_state) if sink_state is not None else None
            )
            edge.barrier_forward = (
                point.energy_hartree - source_energy
                if point.energy_hartree is not None and source_energy is not None
                else None
            )
            edge.barrier_reverse = (
                point.energy_hartree - sink_energy
                if point.energy_hartree is not None and sink_energy is not None
                else None
            )
            edge.fidelity = fidelity

    def _enrich_high_fidelity_thermochemistry(
        self,
        candidate_id: str,
    ) -> dict[str, str] | None:
        if self.thermochemistry_provider is None:
            return None
        point = next(
            (
                current
                for current in self.study.stationary_points
                if current.point_id == candidate_id
            ),
            None,
        )
        if point is None or point.energy_hartree is None:
            return None
        freq_log = next(
            (
                Path(artifact.path)
                for artifact in point.artifacts
                if artifact.kind == "refinement_freq_output"
            ),
            None,
        )
        if freq_log is None:
            return {
                "candidate_id": candidate_id,
                "error": "Frequency artifact unavailable for thermochemistry enrichment",
            }
        runner_meta = self.study.metadata.get("study_runner")
        config = dict(runner_meta.get("config") or {}) if isinstance(runner_meta, dict) else {}
        parent_state = self.study.get_state(point.state_id) if point.state_id is not None else None
        try:
            result = self.thermochemistry_provider.compute(
                sp_energy=point.energy_hartree,
                freq_log=freq_log,
                ensemble=(parent_state.ensemble if parent_state is not None else None),
                temperature=298.15,
                standard_state=resolve_standard_state(config),
            )
        except Exception as exc:
            logger.warning("S4 thermochemistry enrichment failed for %s: %s", candidate_id, exc)
            return {"candidate_id": candidate_id, "error": str(exc)}
        high_fidelity = self._profile_name(self.high_fidelity_profile)
        for index, current in enumerate(self.study.stationary_points):
            if current.point_id != candidate_id:
                continue
            metadata = dict(current.metadata)
            metadata["thermochemistry"] = result.to_dict()
            thermo_history = dict(metadata.get("thermochemistry_by_fidelity") or {})
            thermo_history[high_fidelity] = result.to_dict()
            metadata["thermochemistry_by_fidelity"] = thermo_history
            self.study.stationary_points[index] = replace(current, metadata=metadata)
            break
        return None

    def _run_frontier_loop(self) -> None:
        self._emit_event(
            "SR",
            "frontier_loop_started",
            frontier_size=len(self.study.frontier.queue),
        )
        engine = self._new_step_engine()
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
            ctx = engine.prepare(source_state, route, depth)
            if engine.already_done(ctx):
                continue

            outcome = engine.run(ctx)
            if outcome.canonical_ts is None:
                continue

            if outcome.needs_review:
                irc_result = outcome.irc_result
                endpoint_match = outcome.endpoint_match
                decision_type = outcome.decision_type
                if irc_result is None or endpoint_match is None or decision_type is None:
                    raise RuntimeError("Reviewable step is missing IRC endpoint evidence")
                # Backward-compat contract: only AMBIGUOUS verdicts use the legacy
                # frontier-review type; policy-driven pauses become SR cycle reviews.
                self._create_decision(
                    source_state=source_state,
                    route=route,
                    depth=depth,
                    route_fingerprint=ctx.route_fingerprint,
                    path_result=outcome.path_result,
                    refinement_manifest=outcome.refinement_manifest,
                    irc_result=irc_result,
                    endpoint_match=endpoint_match,
                    decision_type=decision_type,
                )
                self.study.status = "waiting"
                self._persist_study_bundle()
                run_gates(self.study)
                self._persist_study_bundle()
                return

            irc_result = outcome.irc_result
            endpoint_match = outcome.endpoint_match
            if irc_result is None or endpoint_match is None:
                raise RuntimeError("Completed step is missing IRC endpoint evidence")
            self._apply_endpoint_match(
                source_state=source_state,
                route=route,
                depth=depth,
                route_fingerprint=ctx.route_fingerprint,
                canonical_ts=outcome.canonical_ts,
                irc_result=irc_result,
                endpoint_match=endpoint_match,
            )
            persist_route_manifest(
                self.layout,
                source_state_id=source_state_id,
                route=route,
                route_fingerprint=ctx.route_fingerprint,
                status="completed",
                path_result=outcome.path_result,
                refinement_manifest=outcome.refinement_manifest,
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

    def _new_step_engine(self) -> ElementaryStepEngine:
        """Build the ElementaryStepEngine bound to the current study."""
        return ElementaryStepEngine(
            self.study,
            self.layout,
            path_strategy=self.path_strategy,
            refinement_provider=self.refinement_provider,
            endpoint_provider=self.endpoint_provider,
            ensemble_profile=self.ensemble_profile,
            low_fidelity_profile=self.low_fidelity_profile,
            require_review=self.require_review,
            require_sr_review=self.require_sr_review,
            event_log=self.event_log,
        )

    def _normalize_state_ensemble(
        self,
        state: StableState,
        *,
        state_index: int | None = None,
    ) -> StableState:
        state_dir = self.layout.states_root / state.state_id
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
            mark_route_status(
                self.study,
                exploration_key(source_state.state_id, route.route_id),
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
        mark_route_status(
            self.study,
            exploration_key(source_state.state_id, route.route_id),
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
        decision_type: str = "mechanism_frontier_review",
    ) -> DecisionPoint:
        decision_id = f"decision_{len(self.study.decision_points) + 1:03d}"
        payload = {
            "source_state_id": source_state.state_id,
            "route_id": route.route_id,
            "depth": depth,
            "cycle": self.study.cycle_index,
            "endpoint_match": endpoint_match.to_dict(),
            "irc_result": irc_result.to_dict(),
        }
        if decision_type == "sr_cycle_review":
            options = ["continue", "reject_path", "accept_network"]
        else:
            options = ["continue", "promote_to_s4", "stop_branch", "edit_route"]
        legacy_type = "mechanism_frontier_review"
        decision = DecisionPoint(
            id=decision_id,
            type="sr_cycle_review" if decision_type == "sr_cycle_review" else legacy_type,
            status="waiting",
            options=options,
            payload=payload,
            created_at=_utc_now(),
        )
        self.study.decision_points.append(decision)
        self.study.metadata.setdefault("pending_decisions", {})[decision.id] = {
            "source_state_id": source_state.state_id,
            "route_id": route.route_id,
            "depth": depth,
            "cycle": self.study.cycle_index,
            "route_fingerprint": route_fingerprint,
            "exploration_key": exploration_key(source_state.state_id, route.route_id),
            "path_result": path_result.to_dict(),
            "refinement_manifest": refinement_manifest.to_dict(),
            "irc_result": irc_result.to_dict(),
            "endpoint_match": endpoint_match.to_dict(),
            "review": self._build_cycle_review_summary(
                source_state=source_state,
                route=route,
                depth=depth,
                path_result=path_result,
                refinement_manifest=refinement_manifest,
                irc_result=irc_result,
                endpoint_match=endpoint_match,
            ),
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

    def _build_cycle_review_summary(
        self,
        *,
        source_state: StableState,
        route: MechanismRoute,
        depth: int,
        path_result: PathResult,
        refinement_manifest: RefinementManifest,
        irc_result: IrcResult,
        endpoint_match: EndpointMatchResult,
    ) -> dict[str, Any]:
        canonical_ts = refinement_manifest.canonical_winner
        return {
            "cycle": self.study.cycle_index,
            "source_state_id": source_state.state_id,
            "route_id": route.route_id,
            "path_strategy": route.path_strategy,
            "fidelity": route.fidelity,
            "depth": depth,
            "n_path_points": len(path_result.points),
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "kind": candidate.kind,
                    "point_id": candidate.point_id,
                    "score": candidate.score,
                    "progress": candidate.progress,
                }
                for candidate in path_result.candidates
            ],
            "canonical_ts_id": canonical_ts.point_id if canonical_ts is not None else None,
            "canonical_ts_energy_hartree": (
                canonical_ts.energy_hartree if canonical_ts is not None else None
            ),
            "endpoint_verdict": endpoint_match.verdict,
            "endpoint_state_id": endpoint_match.state_id,
            "irc_success": irc_result.success,
            "irc_complete": irc_result.complete,
            "locked_reaction_hash": self.study.metadata.get("locked_reaction_hash"),
        }

    def _apply_sr_revision(
        self,
        decision: DecisionPoint,
        context: dict[str, Any],
        pending: dict[str, Any],
        resolution_payload: dict[str, Any],
    ) -> None:
        revision_data = resolution_payload.get("revision")
        if not isinstance(revision_data, dict):
            raise ValueError(
                f"sr_revision resolution for {decision.id} is missing the revision payload"
            )
        cycle_id = resolution_payload.get("cycle_id")
        if cycle_id is not None and int(cycle_id) != self.study.cycle_index:
            logger.warning(
                "Skipping stale SR revision for %s: resolution cycle %s != study cycle %s",
                decision.id,
                cycle_id,
                self.study.cycle_index,
            )
            return
        decision_kind = str(revision_data.get("decision") or "continue")
        parent_state_id = str(
            revision_data.get("parent_state") or context.get("source_state_id") or ""
        )
        parent_state = self._require_state(parent_state_id)
        selected_bonds = self._parse_selected_bonds(revision_data.get("selected_bonds"))
        audit_notes = self._revision_off_definition_notes(selected_bonds)

        if decision_kind == "continue":
            new_cycle = self.study.cycle_index + 1
            route = self._revision_route_from_bonds(parent_state, selected_bonds, new_cycle)
            if route is None:
                return
            revision = self._build_revision_record(
                decision, revision_data, parent_state_id, selected_bonds, new_cycle
            )
            self._archive_cycle_frontier(revision.revision_id, context)
            self.study.cycle_index = new_cycle
            self.study.cycles.append(
                StudyCycle(
                    cycle_index=new_cycle,
                    revision_id=revision.revision_id,
                    seeded_from_state=parent_state_id,
                    route_ids=[route.route_id],
                    status="running",
                )
            )
            self.study.revisions.append(revision)
            self.study.routes.append(route)
            self.study.frontier = ExplorationFrontier(max_depth=self.study.frontier.max_depth)
            self.study.frontier.push(parent_state_id, route.route_id, depth=0)
            self._persist_cycle_revision(new_cycle, revision)
            self._emit_event(
                "SR",
                "cycle_started",
                cycle=new_cycle,
                revision_id=revision.revision_id,
                parent_state_id=parent_state_id,
                route_id=route.route_id,
            )
        elif decision_kind == "reject_path":
            revision = self._build_revision_record(
                decision,
                revision_data,
                parent_state_id,
                selected_bonds,
                self.study.cycle_index,
            )
            self.study.revisions.append(revision)
            mark_route_status(
                self.study,
                str(context.get("exploration_key") or ""),
                str(context.get("route_fingerprint") or ""),
                status="stopped",
            )
            self._persist_cycle_revision(self.study.cycle_index, revision)
            self._emit_event(
                "SR",
                "path_rejected",
                revision_id=revision.revision_id,
                route_id=context.get("route_id"),
            )
        elif decision_kind == "accept_network":
            revision = self._build_revision_record(
                decision,
                revision_data,
                parent_state_id,
                selected_bonds,
                self.study.cycle_index,
            )
            self.study.revisions.append(revision)
            self.study.frontier.queue.clear()
            self._persist_cycle_revision(self.study.cycle_index, revision)
            self._emit_event("SR", "network_accepted", revision_id=revision.revision_id)
        else:
            raise ValueError(f"Unsupported SR revision decision: {decision_kind!r}")

        if audit_notes:
            audit = self.study.metadata.setdefault("revision_audit", {})
            audit[revision.revision_id] = audit_notes
        decision.status = "resolved"
        decision.resolution = f"sr_revision:{decision_kind}"
        decision.resolved_at = _utc_now()
        pending.pop(decision.id, None)
        self._persist_decision(decision)
        if decision_kind == "reject_path" and self.study.frontier.empty():
            self._create_reseed_decision(parent_state, pending)

    def _parse_selected_bonds(self, payload: Any) -> list[SelectedBond]:
        bonds: list[SelectedBond] = []
        for entry in payload or []:
            if not isinstance(entry, dict):
                continue
            atoms = entry.get("atoms") or []
            if len(atoms) != 2:
                continue
            action = str(entry.get("action") or "stretch")
            if action not in {"stretch", "form", "keep"}:
                action = "stretch"
            start = entry.get("start")
            target = entry.get("target")
            bonds.append(
                SelectedBond(
                    atoms=(int(atoms[0]), int(atoms[1])),
                    action=cast("Any", action),
                    start=float(start) if start is not None else None,
                    target=float(target) if target is not None else None,
                )
            )
        return bonds

    def _build_revision_record(
        self,
        decision: DecisionPoint,
        revision_data: dict[str, Any],
        parent_state_id: str,
        selected_bonds: list[SelectedBond],
        cycle: int,
    ) -> MechanismRevision:
        return MechanismRevision(
            revision_id=str(revision_data.get("revision_id") or f"rev_{cycle:02d}_{decision.id}"),
            study_id=self.study.study_id,
            cycle=cycle,
            parent_state=parent_state_id,
            selected_bonds=selected_bonds,
            decision=cast("Any", str(revision_data.get("decision") or "continue")),
            comment=str(revision_data.get("comment") or ""),
            config_hash=str(revision_data.get("config_hash") or ""),
            created_at=_utc_now(),
        )

    def _revision_off_definition_notes(self, selected_bonds: list[SelectedBond]) -> list[str]:
        if not self.study.metadata.get("locked_reaction_hash"):
            return []
        try:
            from .reaction_definition import read_reaction_json

            definition = read_reaction_json(self.study_dir)
        except (OSError, ValueError) as exc:
            logger.warning("Revision audit could not load reaction.json: %s", exc)
            return []
        if definition is None:
            return []
        defined_pairs = {
            tuple(sorted(pair))
            for change in definition.bond_changes
            for pair in (change.reactant_atoms, change.product_atoms)
            if pair is not None
        }
        notes = [
            f"bond {bond.atoms[0]}-{bond.atoms[1]} ({bond.action}) "
            "is outside the locked reaction definition"
            for bond in selected_bonds
            if bond.action != "keep" and tuple(sorted(bond.atoms)) not in defined_pairs
        ]
        if notes:
            logger.warning("SR revision deviates from locked reaction definition: %s", notes)
        return notes

    def _revision_route_from_bonds(
        self,
        parent_state: StableState,
        selected_bonds: list[SelectedBond],
        new_cycle: int,
    ) -> MechanismRoute | None:
        geometry = self._state_geometry(parent_state)
        if geometry is None:
            logger.warning(
                "Revision route aborted: no geometry for state %s", parent_state.state_id
            )
            return None
        symbols, coords = geometry
        n_atoms = len(symbols)
        specs: list[CoordinateSpec] = []
        for index, bond in enumerate(selected_bonds, start=1):
            i, j = bond.atoms
            if not (0 <= i < n_atoms and 0 <= j < n_atoms):
                logger.warning(
                    "Revision bond %s out of range for state %s (%d atoms)",
                    bond.atoms,
                    parent_state.state_id,
                    n_atoms,
                )
                return None
            current = float(np.linalg.norm(coords[i] - coords[j]))
            if bond.action in {"stretch", "form"}:
                if bond.target is None or bond.target <= 0:
                    logger.warning(
                        "Revision bond %s action %s requires a positive target distance",
                        bond.atoms,
                        bond.action,
                    )
                    return None
                specs.append(
                    CoordinateSpec(
                        id=f"rb{index}",
                        kind="distance",
                        atoms=(i, j),
                        role="drive",
                        start=current,
                        end=float(bond.target),
                    )
                )
            else:
                specs.append(
                    CoordinateSpec(
                        id=f"rb{index}",
                        kind="distance",
                        atoms=(i, j),
                        role="freeze",
                        start=current,
                    )
                )
        if not any(spec.role == "drive" for spec in specs):
            logger.warning(
                "Revision for state %s produced no drive coordinate", parent_state.state_id
            )
            return None
        runner_meta = self.study.metadata.get("study_runner") or {}
        points = int(runner_meta.get("scan_points") or 21)
        route_id = f"cycle{new_cycle}_route_1"
        suffix = 1
        while self._find_route(route_id) is not None:
            suffix += 1
            route_id = f"cycle{new_cycle}_route_{suffix}"
        plan = ReactionCoordinatePlan(
            coordinates=tuple(specs),
            points=points,
            coupling="synchronous",
            start_from="reactant",
        )
        return MechanismRoute(
            route_id=route_id,
            coordinate_plan=plan,
            path_strategy=str(runner_meta.get("strategy") or "guided-scan"),
            fidelity=str(runner_meta.get("fidelity") or "s3"),
            reactant_id=parent_state.state_id,
            product_id=self.study.product_id,
            label=f"SR revision cycle {new_cycle}",
        )

    def _state_geometry(self, state: StableState) -> tuple[list[str], Any] | None:
        path = Path(state.canonical_geometry.path)
        if not path.is_absolute():
            path = self.study_dir / path
        if path.exists():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                count = int(lines[0].strip())
                symbols: list[str] = []
                coords = []
                for line in lines[2 : 2 + count]:
                    parts = line.split()
                    symbols.append(parts[0])
                    coords.append([float(parts[1]), float(parts[2]), float(parts[3])])
                return symbols, np.array(coords, dtype=float)
            except (OSError, ValueError, IndexError) as exc:
                logger.warning("Failed to read geometry for state %s: %s", state.state_id, exc)
        symbols_meta = state.metadata.get("symbols")
        coords_meta = state.metadata.get("coordinates")
        if (
            isinstance(symbols_meta, list)
            and isinstance(coords_meta, list)
            and symbols_meta
            and len(symbols_meta) == len(coords_meta)
        ):
            return [str(symbol) for symbol in symbols_meta], np.array(coords_meta, dtype=float)
        return None

    def _archive_cycle_frontier(self, revision_id: str, context: dict[str, Any]) -> None:
        archive = self.study.metadata.setdefault("cycle_archive", {})
        archive[str(self.study.cycle_index)] = {
            "frontier": self.study.frontier.to_dict(),
            "paused_route_id": context.get("route_id"),
            "revision_id": revision_id,
            "archived_at": _utc_now(),
        }

    def _persist_cycle_revision(self, cycle: int, revision: MechanismRevision) -> None:
        cycle_dir = self.study_dir / "cycles" / f"cycle_{cycle:02d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(cycle_dir / "revision.json", revision.to_dict())

    def _create_reseed_decision(
        self, parent_state: StableState, pending: dict[str, Any]
    ) -> DecisionPoint:
        decision_id = f"decision_{len(self.study.decision_points) + 1:03d}"
        decision = DecisionPoint(
            id=decision_id,
            type="sr_cycle_review",
            status="waiting",
            options=["continue", "accept_network"],
            payload={
                "source_state_id": parent_state.state_id,
                "cycle": self.study.cycle_index,
                "reseed": True,
            },
            created_at=_utc_now(),
        )
        self.study.decision_points.append(decision)
        pending[decision.id] = {
            "source_state_id": parent_state.state_id,
            "cycle": self.study.cycle_index,
            "reseed": True,
            "review": {
                "cycle": self.study.cycle_index,
                "source_state_id": parent_state.state_id,
                "reseed": True,
            },
        }
        self._persist_decision(decision)
        self._emit_event(
            "SR",
            "reseed_requested",
            decision_id=decision.id,
            source_state_id=parent_state.state_id,
        )
        return decision

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


__all__ = ["StudyOrchestrator"]
