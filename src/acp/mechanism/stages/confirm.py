"""Shared confirmation engine for Lowconfirm (S3) and Highconfirm (S4).

One engine, two profiles (plan §7) — ``LowConfirmProfile`` and
``HighConfirmProfile`` differ only in fidelity level and whether the
preliminary IRC validation runs. The scientific implementation (ORCA input
generation, rescue matrix, optimization parsing, independent frequency,
canonical candidate selection) lives in
:class:`acp.mechanism.providers.native_refinement.NativeRefinementProvider`
and is never duplicated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.confsearch.shared.artifacts import write_json_atomic

from ..models import StationaryPoint, StationaryPointRequest
from ..presets import FIDELITY_PROFILES, FidelityProfile, resolve_fidelity
from ..providers.contracts import (
    EndpointProvider,
    IrcResult,
    RefinementManifest,
    RefinementProvider,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LowConfirmProfile:
    """Coarse confirmation profile (plan §7 example values)."""

    opt_method: str = "B97-3c"
    opt_basis: str = ""
    freq_method: str = "B97-3c"
    freq_basis: str = ""
    sp_method: str = "r2SCAN-3c"
    sp_basis: str = ""
    max_cycles: int = 60
    run_irc: bool = True

    @property
    def level(self) -> str:
        return "s3"

    def fidelity_profile(self) -> FidelityProfile:
        return FIDELITY_PROFILES[resolve_fidelity(self.level)]


@dataclass(frozen=True)
class HighConfirmProfile:
    """Fine confirmation profile (plan §7 example values)."""

    opt_method: str = "M062X"
    opt_basis: str = "def2-SVP"
    freq_method: str = "M062X"
    freq_basis: str = "def2-SVP"
    sp_method: str = "wB97M-V"
    sp_basis: str = "def2-TZVPP"
    max_cycles: int = 200
    run_irc: bool = False

    @property
    def level(self) -> str:
        return "s4"

    def fidelity_profile(self) -> FidelityProfile:
        return FIDELITY_PROFILES[resolve_fidelity(self.level)]


ConfirmProfile = LowConfirmProfile | HighConfirmProfile


@dataclass
class ConfirmCandidate:
    """One confirmed (or failed) stationary-point candidate."""

    candidate_id: str
    kind: str
    role: str
    status: str
    input_xyz: str = ""
    optimized_xyz: str = ""
    opt_converged: bool = False
    frequency: dict[str, Any] = field(default_factory=dict)
    sp_energy_hartree: float | None = None
    gibbs_hartree: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.candidate_id,
            "kind": self.kind,
            "role": self.role,
            "status": self.status,
            "input_xyz": self.input_xyz,
            "optimized_xyz": self.optimized_xyz,
            "opt_converged": self.opt_converged,
            "frequency": dict(self.frequency),
            "sp_energy_hartree": self.sp_energy_hartree,
            "gibbs_hartree": self.gibbs_hartree,
            "evidence": dict(self.evidence),
        }


@dataclass
class ConfirmRunOutcome:
    """Aggregated outcome of one ConfirmEngine run."""

    profile_level: str
    candidates: list[ConfirmCandidate] = field(default_factory=list)
    refinement_manifest: RefinementManifest | None = None
    irc: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def confirmed_ids(self) -> list[str]:
        return [c.candidate_id for c in self.candidates if c.status == "confirmed"]


class ConfirmEngine:
    """Run low/high-fidelity confirmation over selected candidates (§7)."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        work_root: Path | None = None,
        profile: ConfirmProfile | None = None,
        refinement_provider: RefinementProvider | None = None,
        endpoint_provider: EndpointProvider | None = None,
    ) -> None:
        self.config = config
        self.profile = profile or LowConfirmProfile()
        self.work_root = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        if refinement_provider is not None:
            self._refinement_provider: RefinementProvider = refinement_provider
        else:
            from ..providers.native_refinement import NativeRefinementProvider

            self._refinement_provider = NativeRefinementProvider(
                config=config, work_root=self.work_root / self.profile.level
            )
        self._endpoint_provider = endpoint_provider

    def confirm(self, requests: list[StationaryPointRequest]) -> ConfirmRunOutcome:
        """Refine all requests at the profile's fidelity; optionally IRC the TS."""
        outcome = ConfirmRunOutcome(profile_level=self.profile.level)
        if not requests:
            outcome.errors.append("No candidates selected for confirmation")
            return outcome

        fidelity = self.profile.fidelity_profile()
        manifest = self._refinement_provider.refine(requests, fidelity)
        outcome.refinement_manifest = manifest
        by_request = {attempt.request_id: attempt for attempt in manifest.attempts}
        point_by_id = {point.point_id: point for point in _manifest_points(manifest)}

        for request in requests:
            attempt = by_request.get(request.id)
            point = point_by_id.get(request.id)
            if attempt is not None and point is not None and attempt.status == "success":
                outcome.candidates.append(
                    self._candidate_from_point(request, point, attempt.evidence)
                )
            else:
                evidence = dict(attempt.evidence) if attempt is not None else {}
                outcome.candidates.append(
                    ConfirmCandidate(
                        candidate_id=request.id,
                        kind=request.kind,
                        role=request.role,
                        status="failed",
                        evidence=evidence,
                    )
                )
                outcome.errors.append(f"Candidate {request.id} failed refinement")

        outcome.candidates.sort(key=lambda c: (c.status != "confirmed", c.candidate_id))
        if self.profile.run_irc:
            outcome.irc = self._run_irc_for_canonical(manifest, fidelity)
        return outcome

    def _candidate_from_point(
        self,
        request: StationaryPointRequest,
        point: StationaryPoint,
        evidence: dict[str, Any],
    ) -> ConfirmCandidate:
        thermo = point.metadata.get("thermochemistry")
        gibbs = None
        if isinstance(thermo, dict):
            gibbs = thermo.get("g_composite_hartree")
        identity = point.identity
        frequency: dict[str, Any] = {
            "status": str(point.metadata.get("frequency_status") or "unknown"),
            "imaginary_frequency_cm1": (
                identity.imaginary_frequency_cm1 if identity is not None else None
            ),
            "n_imaginary": identity.imaginary_count if identity is not None else None,
            "valid_ts_identity": identity.valid if identity is not None else None,
        }
        return ConfirmCandidate(
            candidate_id=request.id,
            kind=point.kind,
            role=point.role,
            status="confirmed",
            input_xyz=str(request.input_geometry.path),
            optimized_xyz=_point_canonical_xyz(point),
            opt_converged=str(point.metadata.get("opt_status") or "") == "complete",
            frequency=frequency,
            sp_energy_hartree=point.energy_hartree,
            gibbs_hartree=gibbs,
            evidence=dict(evidence),
        )

    def _run_irc_for_canonical(
        self,
        manifest: RefinementManifest,
        fidelity: FidelityProfile,
    ) -> dict[str, Any] | None:
        winner = manifest.canonical_winner
        if winner is None or winner.kind != "ts":
            return None
        if self._endpoint_provider is None:
            from acp.backends import get_backend

            from ..endpoint import DefaultEndpointProvider, EndpointMatchThresholds

            self._endpoint_provider = DefaultEndpointProvider(
                backend=get_backend("orca")(self.config or {}),
                thresholds=EndpointMatchThresholds(),
                work_root=self.work_root / "irc",
            )
        try:
            irc_result = self._endpoint_provider.run_irc(winner, fidelity)
        except Exception as exc:  # noqa: BLE001 - IRC is advisory at S3
            logger.warning("IRC validation failed for %s: %s", winner.point_id, exc)
            return {"enabled": True, "complete": False, "error": str(exc)}
        return _irc_block(irc_result)


def _manifest_points(manifest: RefinementManifest) -> list[StationaryPoint]:
    points: list[StationaryPoint] = []
    for attempt in manifest.attempts:
        if attempt.stationary_point is not None:
            points.append(attempt.stationary_point)
    winner = manifest.canonical_winner
    if winner is not None and all(point.point_id != winner.point_id for point in points):
        points.append(winner)
    return points


def _point_canonical_xyz(point: StationaryPoint) -> str:
    canonical = Path(point.geometry.path)
    if canonical.is_file():
        return str(canonical)
    for artifact in point.artifacts:
        if artifact.kind.endswith("geometry") and Path(artifact.path).is_file():
            return artifact.path
    metadata_xyz = point.metadata.get("canonical_xyz")
    return str(metadata_xyz) if metadata_xyz else point.geometry.path


def _irc_block(irc_result: IrcResult) -> dict[str, Any]:
    block: dict[str, Any] = {
        "irc_id": irc_result.irc_id,
        "ts_id": irc_result.ts_id,
        "complete": bool(irc_result.complete),
        "endpoints": {},
    }
    for direction, artifact in (
        ("forward", irc_result.forward_endpoint),
        ("reverse", irc_result.reverse_endpoint),
    ):
        if artifact is None:
            continue
        block["endpoints"][direction] = {
            "xyz": artifact.path,
            "sha256": artifact.sha256,
            "kind": artifact.kind,
        }
    return block


def write_stage_manifest(path: Path, payload: dict[str, Any]) -> Path:
    return write_json_atomic(path, payload)


__all__ = [
    "ConfirmCandidate",
    "ConfirmEngine",
    "ConfirmProfile",
    "ConfirmRunOutcome",
    "HighConfirmProfile",
    "LowConfirmProfile",
    "write_stage_manifest",
]
