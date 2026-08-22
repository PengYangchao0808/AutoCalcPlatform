"""Tests for the standalone mechanism module interchange contracts.

Covers :mod:`acp.mechanism.modules.schema`: ModuleStatus mapping,
ResolvedEndpoint resolution, ElementaryStepRequest/Manifest round-trips and
the generic ModuleManifest envelope with content hashing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.mechanism.models import (
    ArtifactRef,
    ReactionCoordinatePlan,
    StableState,
)
from acp.mechanism.modules.schema import (
    ElementaryStepManifest,
    ElementaryStepRequest,
    FailureRecord,
    ModuleManifest,
    ResolvedEndpoint,
    read_elementary_step_manifest,
    read_module_manifest,
    step_top_status,
    write_elementary_step_manifest,
    write_module_manifest,
)
from cccp.qc.interfaces.constraints import CoordinateSpec


def _sample_plan() -> ReactionCoordinatePlan:
    return ReactionCoordinatePlan(
        coordinates=[
            CoordinateSpec(
                id="rc1",
                kind="distance",
                atoms=(0, 1),
                role="drive",
                start=1.5,
                end=3.4,
            )
        ],
        points=11,
        coupling="synchronous",
        start_from="reactant",
    )


def _sample_state(state_id: str = "R1") -> StableState:
    return StableState(
        state_id=state_id,
        role="reactant",
        canonical_geometry=ArtifactRef(path=f"{state_id}.xyz", sha256="abc", kind="xyz"),
        charge=0,
        multiplicity=1,
        identity_fingerprint="fp",
    )


class TestStepTopStatus:
    def test_validated(self) -> None:
        assert step_top_status("VALIDATED") == "validated"

    def test_ambiguous_maps_to_waiting_review(self) -> None:
        assert step_top_status("AMBIGUOUS_ENDPOINT") == "waiting_review"

    @pytest.mark.parametrize(
        "internal",
        [
            "FAILED_PATH",
            "FAILED_REFINEMENT",
            "FAILED_TS_VALIDATION",
            "FAILED_IRC",
            "FAILED_ENDPOINT_VALIDATION",
        ],
    )
    def test_failures(self, internal: str) -> None:
        assert step_top_status(internal) == "failed"

    @pytest.mark.parametrize("internal", ["PATH_RUNNING", "TS_FOUND", "IRC_COMPLETE", None])
    def test_partial(self, internal: str | None) -> None:
        assert step_top_status(internal) == "partial"


class TestResolvedEndpoint:
    def test_round_trip(self) -> None:
        endpoint = ResolvedEndpoint(
            endpoint_id="ep_fwd",
            direction="forward",
            role="source",
            raw_geometry=ArtifactRef(path="irc/fwd.xyz", sha256="s1", kind="xyz"),
            minimum_validated=True,
            match_verdict="MATCH_EXISTING",
            matched_state_id="R1",
        )
        restored = ResolvedEndpoint.from_dict(endpoint.to_dict())
        assert restored.endpoint_id == "ep_fwd"
        assert restored.direction == "forward"
        assert restored.role == "source"
        assert restored.matched_state_id == "R1"
        assert restored.minimum_validated is True

    def test_sink_endpoint(self) -> None:
        endpoint = ResolvedEndpoint(
            endpoint_id="ep_rev",
            direction="reverse",
            role="sink",
            raw_geometry=ArtifactRef(path="irc/rev.xyz", sha256="s2", kind="xyz"),
            match_verdict="NEW_STATE",
        )
        assert endpoint.to_dict()["role"] == "sink"
        assert endpoint.to_dict()["match_verdict"] == "NEW_STATE"


class TestElementaryStepRequest:
    def test_round_trip(self) -> None:
        request = ElementaryStepRequest(
            step_id="step_001",
            source_state=_sample_state("R1"),
            target_state=_sample_state("P1"),
            coordinate_plan=_sample_plan(),
            path_strategy="rph-reverse",
            refinement_fidelity="s3",
        )
        restored = ElementaryStepRequest.from_dict(request.to_dict())
        assert restored.step_id == "step_001"
        assert restored.source_state.state_id == "R1"
        assert restored.target_state is not None
        assert restored.target_state.state_id == "P1"
        assert restored.coordinate_plan.points == 11

    def test_target_optional(self) -> None:
        request = ElementaryStepRequest(
            step_id="step_002",
            source_state=_sample_state("R1"),
            coordinate_plan=_sample_plan(),
            path_strategy="guided-scan",
        )
        restored = ElementaryStepRequest.from_dict(request.to_dict())
        assert restored.target_state is None


class TestElementaryStepManifest:
    def test_validated_flag(self) -> None:
        manifest = ElementaryStepManifest(
            step_id="step_001",
            status="validated",
            gates={"G2": "PASS", "G3": "PASS", "G4": "PASS"},
        )
        assert manifest.is_validated is True

    def test_partial_with_failure(self) -> None:
        manifest = ElementaryStepManifest(
            step_id="step_002",
            status="partial",
            furthest_stage="path",
            failure=FailureRecord(
                stage="refinement",
                reason="no_canonical_stationary_point",
                recoverable=True,
            ),
            suggested_actions=["retry_refinement", "change_seed", "manual_takeover"],
        )
        assert manifest.is_validated is False
        assert manifest.to_dict()["failure"]["reason"] == "no_canonical_stationary_point"

    def test_disk_round_trip(self, tmp_path: Path) -> None:
        manifest = ElementaryStepManifest(
            step_id="step_003",
            status="validated",
            irc={
                "irc_id": "irc_001",
                "endpoints": {
                    "forward": {"role": "source", "verdict": "MATCH_EXISTING"},
                    "reverse": {"role": "sink", "verdict": "NEW_STATE"},
                },
            },
        )
        path = write_elementary_step_manifest(tmp_path, manifest)
        restored = read_elementary_step_manifest(path)
        assert restored.step_id == "step_003"
        assert restored.status == "validated"
        assert restored.irc["endpoints"]["reverse"]["role"] == "sink"


class TestModuleManifest:
    def test_content_hash_stable(self) -> None:
        manifest = ModuleManifest(
            phase="conformer",
            output={"ensemble_xyz": "conformers.xyz"},
        )
        assert manifest.content_hash.startswith("sha256:")
        assert manifest.content_hash == manifest.content_hash

    def test_disk_round_trip(self, tmp_path: Path) -> None:
        manifest = ModuleManifest(
            phase="elementary_step",
            status="partial",
            failure=FailureRecord(stage="irc", reason="irc_incomplete", recoverable=True),
        )
        path = write_module_manifest(tmp_path, manifest)
        restored = read_module_manifest(path)
        assert restored.phase == "elementary_step"
        assert restored.status == "partial"
        assert restored.failure is not None
        assert restored.failure.reason == "irc_incomplete"
