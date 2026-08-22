"""Standalone high-fidelity confirmation engine for ``mech-confirm`` (M3)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from cccp.qc.interfaces.constraints import ReactionCoordinatePlan

from ..models import ArtifactRef, Provenance, StationaryPoint, StationaryPointRequest
from ..presets import FIDELITY_PROFILES, resolve_fidelity
from ..providers.contracts import RefinementProvider

if TYPE_CHECKING:
    from ..modules.schema import ElementaryStepManifest, ResolvedEndpoint

logger = logging.getLogger(__name__)


def _provenance(fidelity: str, manifest: ElementaryStepManifest) -> Provenance:
    return Provenance(
        provider="acp-mech-confirm",
        provider_version="1.0",
        provider_commit="m3",
        strategy="high-fidelity-confirmation",
        strategy_version="1.0",
        profile_id=fidelity,
        schema_version="m0",
        input_signature=str(manifest.provenance.get("fingerprint") or manifest.step_id),
    )


class ConfirmationEngine:
    """Re-refine one selected stationary point at high fidelity (S4)."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        work_root: Path | None = None,
        fidelity: str = "s4",
        refinement_provider: RefinementProvider | None = None,
    ) -> None:
        self.config = config
        self.fidelity = resolve_fidelity(fidelity)
        self.work_root = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        if refinement_provider is not None:
            self._refinement_provider: RefinementProvider = refinement_provider
        else:
            from ..providers.native_refinement import NativeRefinementProvider

            self._refinement_provider = NativeRefinementProvider(
                config=config, work_root=self.work_root / "s4"
            )

    def run(self, manifest: ElementaryStepManifest, select: str) -> StationaryPoint:
        """Refine the selected manifest artifact; return the canonical winner."""
        request = self._build_request(manifest, select)
        profile = FIDELITY_PROFILES[self.fidelity]
        result = self._refinement_provider.refine([request], profile)
        winner = result.canonical_winner
        if winner is None:
            raise ValueError(f"Confirmation refinement produced no canonical winner ({select})")
        return winner

    def _build_request(
        self,
        manifest: ElementaryStepManifest,
        select: str,
    ) -> StationaryPointRequest:
        role: Literal["transition_state", "product", "intermediate"]
        kind: Literal["ts", "minimum"]
        if select == "ts:canonical":
            ts_block = manifest.transition_state or {}
            geometry = self._artifact_from(ts_block, "ts:canonical")
            role = "transition_state"
            kind = "ts"
            request_id = f"{manifest.step_id}_ts_s4"
        elif select == "endpoint:sink":
            endpoint = self._sink_endpoint(manifest)
            geometry = endpoint.raw_geometry
            if endpoint.optimized_minimum is not None:
                geometry = endpoint.optimized_minimum.geometry
            role = self._sink_role(manifest, endpoint.matched_state_id)
            kind = "minimum"
            request_id = f"{manifest.step_id}_sink_s4"
        else:
            raise ValueError(f"Unsupported --select value: {select!r}")

        plan = (
            ReactionCoordinatePlan.from_dict(manifest.coordinate_plan)
            if manifest.coordinate_plan
            else None
        )
        charge = int(manifest.provenance.get("charge") or 0)
        multiplicity = int(manifest.provenance.get("multiplicity") or 1)
        return StationaryPointRequest(
            id=request_id,
            role=role,
            kind=kind,
            input_geometry=geometry,
            coordinate_plan=plan,
            fallback_geometries=[],
            source_stage="S4",
            charge=charge,
            multiplicity=multiplicity,
            atom_mapping=None,
            parent_state_id=(
                str(manifest.provenance.get("source_state_id"))
                if manifest.provenance.get("source_state_id")
                else None
            ),
            route_id=(
                str(manifest.path.get("route_id")) if manifest.path.get("route_id") else None
            ),
            ensemble_correction=None,
            provenance=_provenance(self.fidelity, manifest),
        )

    @staticmethod
    def _artifact_from(block: dict[str, Any], select: str) -> ArtifactRef:
        xyz = block.get("xyz")
        if isinstance(xyz, str) and xyz:
            return ArtifactRef(path=xyz, sha256="", kind="stationary_point_geometry")
        point = block.get("point")
        if isinstance(point, dict) and isinstance(point.get("geometry"), dict):
            return ArtifactRef.from_dict(dict(point["geometry"]))
        raise ValueError(f"Cannot resolve input geometry for select={select!r}")

    @staticmethod
    def _sink_endpoint(manifest: ElementaryStepManifest) -> ResolvedEndpoint:
        from ..modules.schema import ResolvedEndpoint

        irc = manifest.irc or {}
        endpoints = irc.get("endpoints") or {}
        for direction in ("forward", "reverse"):
            data = endpoints.get(direction)
            if isinstance(data, dict):
                resolved = ResolvedEndpoint.from_dict(dict(data))
                if resolved.role == "sink":
                    return resolved
        raise ValueError("Step manifest has no sink endpoint; run mech-step first")

    @staticmethod
    def _sink_role(
        manifest: ElementaryStepManifest,
        matched_state_id: str | None,
    ) -> Literal["product", "intermediate"]:
        if manifest.target_state_id and matched_state_id == manifest.target_state_id:
            return "product"
        return "intermediate"

    def _check_s3_s4_consistency(
        self,
        s3_point: StationaryPoint,
        s4_point: StationaryPoint,
    ) -> list[str]:
        """Compare S3 identity vs S4 identity; empty list = consistent."""
        messages: list[str] = []
        if s3_point.kind != s4_point.kind:
            messages.append(f"kind changed: s3={s3_point.kind} -> s4={s4_point.kind}")
        s3_identity = s3_point.identity
        s4_identity = s4_point.identity
        if s3_identity is not None and s4_identity is not None:
            if s3_identity.imaginary_count != s4_identity.imaginary_count:
                messages.append(
                    "hessian_index not preserved: "
                    f"s3 imaginary_count={s3_identity.imaginary_count} -> "
                    f"s4 imaginary_count={s4_identity.imaginary_count}"
                )
        elif (s3_identity is None) != (s4_identity is None):
            messages.append("identity evidence present at only one fidelity level")
        return cast(list[str], messages)


__all__ = ["ConfirmationEngine"]
