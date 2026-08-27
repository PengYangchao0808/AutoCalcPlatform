"""Deterministic fake providers for M0 mechanism-study verification."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from acp.calculations.contracts import JsonValue
from acp.calculations.irc.contracts import (
    EndpointMatchResult,
    FidelityLike,
    IrcResult,
    StableStateLike,
    TransitionStateLike,
)
from acp.core.models import Structure, StructureEnsemble, StructureRecord

from ..models import (
    ArtifactRef,
    PathCandidate,
    PathPoint,
    PathResult,
    Provenance,
    SeedCandidate,
    StableState,
    StationaryPoint,
    StationaryPointRequest,
    TsIdentity,
)
from .contracts import (
    RefinementAttempt,
    RefinementManifest,
)


def _sha(label: str) -> str:
    return f"sha256:{hashlib.sha256(label.encode('utf-8')).hexdigest()}"


def _artifact(label: str, kind: str) -> ArtifactRef:
    return ArtifactRef(path=f"memory://{label}", sha256=_sha(label), kind=kind)


def _provenance(strategy: str, profile_id: str, signature: str) -> Provenance:
    return Provenance(
        provider="fake",
        provider_version="1.0",
        provider_commit="fake-m0",
        strategy=strategy,
        strategy_version="1.0",
        profile_id=profile_id,
        schema_version="m0",
        input_signature=signature,
    )


@dataclass
class FakeEnsembleProvider:
    """Generate tiny deterministic ensembles without QC."""

    calls: int = 0

    def generate(self, stable_state: StableState, profile: Any) -> StructureEnsemble:
        self.calls += 1
        base_coords = np.asarray(
            stable_state.metadata.get(
                "coordinates",
                [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            ),
            dtype=float,
        )
        symbols = [str(symbol) for symbol in stable_state.metadata.get("symbols", ["C", "H", "H"])]
        n_conformers = 1 + (sum(ord(ch) for ch in stable_state.state_id) % 3)
        records: list[StructureRecord] = []
        for idx in range(n_conformers):
            coords = base_coords + idx * 0.05
            structure = Structure(
                id=f"{stable_state.state_id}_conf_{idx + 1}",
                charge=stable_state.charge,
                multiplicity=stable_state.multiplicity,
                symbols=symbols,
                coordinates=coords,
                metadata={"state_id": stable_state.state_id, "role": stable_state.role},
            )
            records.append(
                StructureRecord(
                    structure=structure,
                    energy_hartree=-100.0 + idx * 0.001,
                    weight=1.0 / n_conformers,
                    properties={"rank": idx + 1},
                )
            )
        return StructureEnsemble(records=records, metadata={"profile": str(profile)})


@dataclass
class FakePathSearchStrategy:
    """Return scripted two-step synthetic path profiles."""

    calls: int = 0
    gate_policies: dict[str, dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.gate_policies is None:
            self.gate_policies = {
                "G2": {
                    "require_complete": True,
                    "min_points": 5,
                    "min_seed_candidates": 1,
                    "require_endpoint_evidence": True,
                }
            }

    def search(
        self,
        source_state: StableState,
        target_state: StableState | None,
        coordinate_plan: Any,
        profile: Any,
    ) -> PathResult:
        self.calls += 1
        step_index = 1 if source_state.role == "reactant" else 2
        points: list[PathPoint] = []
        energies = (
            [0.0, 0.12, 0.36, 0.18, 0.05] if step_index == 1 else [0.0, 0.10, 0.28, 0.11, 0.02]
        )
        for idx, energy in enumerate(energies):
            progress = idx / (len(energies) - 1)
            coords = (
                np.asarray(
                    source_state.metadata.get(
                        "coordinates",
                        [[0.0, 0.0, 0.0], [1.0, 0.1 * idx, 0.0], [0.0, 1.0, 0.0]],
                    ),
                    dtype=float,
                )
                + idx * 0.02
            )
            points.append(
                PathPoint(
                    point_id=f"{source_state.state_id}_p{idx:03d}",
                    progress=progress,
                    coordinate_values=coordinate_plan.coordinate_targets(idx),
                    reaction_coordinates=coordinate_plan.coordinate_targets(idx),
                    geometry=coords,
                    energies_hartree={"fake": energy},
                    frame_index=idx,
                    arc_length=progress,
                    topology_valid=True,
                    diagnostics={"step_index": step_index},
                    provenance=_provenance(
                        "fake-guided-scan",
                        str(profile),
                        _sha(source_state.state_id),
                    ),
                )
            )

        ts_label = f"ts{step_index}"
        int_candidates = []
        path_candidates = [
            PathCandidate(
                candidate_id=f"ts_candidate_{step_index:02d}",
                kind="ts_seed",
                point_id=points[2].point_id,
                reason="synthetic_peak",
                progress=points[2].progress,
                score=10.0 - step_index,
            )
        ]
        seed_candidates = [
            SeedCandidate(
                id=f"seed_{ts_label}",
                kind="ts_seed",
                geometry=_artifact(f"{source_state.state_id}_{ts_label}", "ts_seed_geometry"),
                rank=1,
                selection_mode="synthetic_peak_selector_v1",
                confidence="high",
                evidence={"point_id": points[2].point_id, "energy": energies[2]},
            )
        ]
        if step_index == 1:
            path_candidates.append(
                PathCandidate(
                    candidate_id="int_candidate_01",
                    kind="intermediate_seed",
                    point_id=points[3].point_id,
                    reason="synthetic_knee",
                    progress=points[3].progress,
                    score=1.0,
                )
            )
            int_candidates = [
                SeedCandidate(
                    id="seed_int1",
                    kind="intermediate_seed",
                    geometry=_artifact("int1_geometry", "intermediate_seed_geometry"),
                    rank=1,
                    selection_mode="synthetic_knee_selector_v1",
                    confidence="medium",
                    evidence={"point_id": points[3].point_id, "energy": energies[3]},
                )
            ]

        route_id = source_state.metadata.get("route_id") or "route-main"
        result = PathResult(
            points=points,
            candidates=path_candidates,
            strategy="fake-guided-scan",
            route_id=str(route_id),
            selected_ts_id=path_candidates[0].candidate_id,
            selected_int_id="int_candidate_01" if step_index == 1 else None,
            metadata={"step_index": step_index, "gate_policies": self.gate_policies},
            seed_candidates=seed_candidates + int_candidates,
            strategy_id="fake-guided-scan",
            strategy_version="1.0",
            complete=True,
            endpoint_evidence={
                "target_state_id": (target_state.state_id if target_state is not None else None)
            },
            topology_segments=[{"segment": 1, "valid": True}],
            artifacts={"path_profile": f"fake://{source_state.state_id}/path"},
        )
        return result


@dataclass
class FakeRefinementProvider:
    """Refine requests into canonical fake stationary points."""

    calls: int = 0

    def refine(self, requests: list[StationaryPointRequest], fidelity: Any) -> RefinementManifest:
        self.calls += 1
        attempts: list[RefinementAttempt] = []
        canonical: StationaryPoint | None = None
        fidelity_name = getattr(fidelity, "name", str(fidelity))
        for idx, request in enumerate(requests):
            point_id = f"{request.id}_{fidelity_name}"
            identity = (
                TsIdentity(
                    imaginary_count=1,
                    imaginary_frequency_cm1=-350.0,
                    mode_match_score=0.82,
                    topology_sane=True,
                    valid=True,
                    messages=[],
                )
                if request.kind == "ts"
                else None
            )
            stationary_point = StationaryPoint(
                point_id=point_id,
                role=request.role,
                kind=request.kind,
                geometry=request.input_geometry,
                charge=request.charge,
                multiplicity=request.multiplicity,
                state_id=request.parent_state_id,
                route_id=request.route_id,
                energy_hartree=-150.0 + idx * 0.01,
                identity=identity,
                provenance=_provenance("fake-refinement", fidelity_name, _sha(request.id)),
                metadata={"fidelity": fidelity_name, "confirmed": True},
            )
            attempts.append(
                RefinementAttempt(
                    request_id=request.id,
                    status="success",
                    stationary_point=stationary_point,
                    evidence={"fidelity": fidelity_name},
                )
            )
            if canonical is None:
                canonical = stationary_point
        manifest = RefinementManifest(
            manifest_id=f"ref_{self.calls:03d}",
            canonical_winner=canonical,
            attempts=attempts,
            fidelity=fidelity_name,
            metadata={"n_requests": len(requests)},
        )
        return manifest


@dataclass
class FakeEndpointProvider:
    """Scripted endpoint classifications for A → INT → B."""

    ambiguous_first: bool = False
    irc_calls: int = 0
    classify_calls: int = 0

    def run_irc(self, ts: TransitionStateLike, fidelity: FidelityLike | str) -> IrcResult:
        self.irc_calls += 1
        label = f"irc_{self.irc_calls:03d}_{ts.point_id}"
        return IrcResult(
            irc_id=label,
            ts_id=ts.point_id,
            success=True,
            complete=True,
            forward_endpoint=_artifact(f"{label}_forward", "irc_endpoint"),
            reverse_endpoint=_artifact(f"{label}_reverse", "irc_endpoint"),
            evidence={"fidelity": getattr(fidelity, "name", str(fidelity)), "step": self.irc_calls},
        )

    def classify_endpoints(
        self,
        irc_result: IrcResult,
        known_states: Sequence[StableStateLike],
    ) -> EndpointMatchResult:
        self.classify_calls += 1
        product = next((state for state in known_states if state.role == "product"), None)
        if self.classify_calls == 1:
            geometry = _artifact("state_int_geometry", "stable_state_geometry")
            state_payload: dict[str, JsonValue] = {
                "state_id": "state_int",
                "role": "intermediate",
                "canonical_geometry": {
                    "path": geometry.path,
                    "sha256": geometry.sha256,
                    "kind": geometry.kind,
                },
                "charge": 0,
                "multiplicity": 1,
                "identity_fingerprint": _sha("state_int"),
                "ensemble": None,
                "metadata": {
                    "symbols": ["C", "H", "H"],
                    "coordinates": [[0.0, 0.0, 0.0], [1.1, 0.1, 0.0], [-0.1, 1.0, 0.0]],
                },
            }
            verdict = "AMBIGUOUS" if self.ambiguous_first else "NEW_STATE"
            return EndpointMatchResult(
                verdict=verdict,
                state_id="state_int",
                evidence={
                    "candidate_state": state_payload,
                    "irc_id": irc_result.irc_id,
                    "rmsd": 0.12,
                },
            )
        return EndpointMatchResult(
            verdict="MATCH_EXISTING",
            state_id=product.state_id if product is not None else "state_product",
            evidence={"irc_id": irc_result.irc_id, "matched_role": "product"},
        )


__all__ = [
    "FakeEnsembleProvider",
    "FakeEndpointProvider",
    "FakePathSearchStrategy",
    "FakeRefinementProvider",
]
