"""Provider contracts for the mechanism study layer."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from acp.calculations.irc.contracts import (
    EndpointMatchResult,
    EndpointProvider,
    EndpointVerdict,
    IrcResult,
)
from acp.core.models import StructureEnsemble
from cccp.qc.interfaces.constraints import ReactionCoordinatePlan

from .._helpers import opt_float as _opt_float
from ..models import (
    PathResult,
    StableState,
    StationaryPoint,
    StationaryPointRequest,
)
from ..presets import FidelityProfile

logger = logging.getLogger(__name__)

StableStateEnsemble = StructureEnsemble


def _stable_hash(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


@dataclass
class RefinementAttempt:
    """One refinement attempt for a stationary-point request."""

    request_id: str
    status: Literal["success", "failed"]
    stationary_point: StationaryPoint | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "stationary_point": (
                self.stationary_point.to_dict() if self.stationary_point is not None else None
            ),
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefinementAttempt:
        stationary_point_data = data.get("stationary_point")
        return cls(
            request_id=str(data.get("request_id") or ""),
            status=cast_attempt_status(data.get("status")),
            stationary_point=(
                StationaryPoint.from_dict(dict(stationary_point_data))
                if isinstance(stationary_point_data, dict)
                else None
            ),
            evidence=dict(data.get("evidence") or {}),
        )


@dataclass
class RefinementManifest:
    """Canonical refinement result plus all attempt history."""

    manifest_id: str
    canonical_winner: StationaryPoint | None
    attempts: list[RefinementAttempt] = field(default_factory=list)
    manifest_hash: str = ""
    fidelity: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.manifest_hash:
            self.manifest_hash = _stable_hash(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "manifest_id": self.manifest_id,
            "canonical_winner": (
                self.canonical_winner.to_dict() if self.canonical_winner is not None else None
            ),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "fidelity": self.fidelity,
            "metadata": dict(self.metadata),
        }
        if include_hash:
            payload["manifest_hash"] = self.manifest_hash
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefinementManifest:
        winner_data = data.get("canonical_winner")
        return cls(
            manifest_id=str(data.get("manifest_id") or ""),
            canonical_winner=(
                StationaryPoint.from_dict(dict(winner_data))
                if isinstance(winner_data, dict)
                else None
            ),
            attempts=[
                RefinementAttempt.from_dict(dict(attempt_data))
                for attempt_data in data.get("attempts") or []
            ],
            manifest_hash=str(data.get("manifest_hash") or ""),
            fidelity=str(data.get("fidelity") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class ThermochemistryResult:
    """Thermochemistry output for one stationary point or ensemble."""

    gibbs_hartree: float | None = None
    enthalpy_hartree: float | None = None
    entropy_au: float | None = None
    temperature: float = 298.15
    standard_state: str = "1atm"
    corrections: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gibbs_hartree": self.gibbs_hartree,
            "enthalpy_hartree": self.enthalpy_hartree,
            "entropy_au": self.entropy_au,
            "temperature": self.temperature,
            "standard_state": self.standard_state,
            "corrections": dict(self.corrections),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThermochemistryResult:
        return cls(
            gibbs_hartree=_opt_float(data.get("gibbs_hartree")),
            enthalpy_hartree=_opt_float(data.get("enthalpy_hartree")),
            entropy_au=_opt_float(data.get("entropy_au")),
            temperature=float(data.get("temperature") or 298.15),
            standard_state=str(data.get("standard_state") or "1atm"),
            corrections=dict(data.get("corrections") or {}),
        )


@runtime_checkable
class EnsembleProvider(Protocol):
    """Generate a conformer ensemble for a stable state (S1)."""

    def generate(self, stable_state: StableState, profile: Any) -> StableStateEnsemble:
        """Return a normalized stable-state ensemble."""
        ...


@runtime_checkable
class PathSearchStrategy(Protocol):
    """Search a reaction path between two states (S2)."""

    def search(
        self,
        source_state: StableState,
        target_state: StableState | None,
        coordinate_plan: ReactionCoordinatePlan,
        profile: Any,
    ) -> PathResult:
        """Return a path search result."""
        ...


@runtime_checkable
class RefinementProvider(Protocol):
    """Refine stationary-point requests (S3/S4)."""

    def refine(
        self,
        requests: list[StationaryPointRequest],
        fidelity: FidelityProfile | str,
    ) -> RefinementManifest:
        """Return the refinement manifest with canonical winner and attempts."""
        ...


@runtime_checkable
class ThermochemistryProvider(Protocol):
    """Compute thermochemistry from SP/frequency/ensemble data."""

    def compute(
        self,
        sp_energy: float | None,
        freq_log: Path | str | None,
        ensemble: StableStateEnsemble | None,
        temperature: float,
        standard_state: str,
    ) -> ThermochemistryResult:
        """Return the thermochemistry result."""
        ...


def cast_attempt_status(value: object) -> Literal["success", "failed"]:
    return "success" if str(value) == "success" else "failed"


__all__ = [
    "EndpointMatchResult",
    "EndpointProvider",
    "EndpointVerdict",
    "EnsembleProvider",
    "IrcResult",
    "PathSearchStrategy",
    "RefinementAttempt",
    "RefinementManifest",
    "RefinementProvider",
    "StableStateEnsemble",
    "ThermochemistryProvider",
    "ThermochemistryResult",
]
