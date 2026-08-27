"""Standalone mechanism module interchange contracts.

Schema v1 for the modular mechanism pipeline: ``mech-conf`` / ``mech-step`` /
``mech-confirm``. Each module is an independently runnable unit that reads a
typed request (or CLI args), drives one provider/engine, and persists a
:class:`ModuleManifest` that the next module (or ``mech-chain``) consumes.

Design intent (v3 Elementary Step architecture):

* The minimal chemistry unit is an **elementary step** (path hypothesis ->
  stationary-point evidence -> connectivity confirmation), not any single QC
  step. ``mech-step`` is the Elementary Step Engine.
* IRC endpoints are resolved per direction (forward/reverse) into
  ``source``/``sink`` roles dynamically — IRC direction never implies
  reactant/product identity.
* Failure is **partial, never silently upgraded**: ``status="partial"``
  preserves all intermediate artifacts and lists ``suggested_actions``;
  ``status="validated"`` is reserved for G2+G3+G4 all-PASS.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from acp.calculations.irc.contracts import EndpointVerdict

from ..models import (
    ArtifactRef,
    ReactionCoordinatePlan,
    StableState,
    StationaryPoint,
)


def _serialize_coordinate_plan(plan: ReactionCoordinatePlan) -> dict[str, Any]:
    return {
        "coordinates": [_serialize_coordinate_spec(spec) for spec in plan.coordinates],
        "points": plan.points,
        "coupling": plan.coupling,
        "start_from": plan.start_from,
    }


def _serialize_coordinate_spec(spec: Any) -> dict[str, Any]:
    return {
        "id": spec.id,
        "kind": spec.kind,
        "atoms": list(spec.atoms),
        "role": spec.role,
        "start": spec.start,
        "end": spec.end,
    }


# ---------------------------------------------------------------------------
# Module-level status model
# ---------------------------------------------------------------------------

ModulePhase = Literal["conformer", "elementary_step", "confirmation"]

ModuleStatus = Literal["validated", "partial", "failed", "waiting_review"]

# Elementary Step Engine internal state machine (persisted in manifest.history)
STEP_STATUS_FLOW: tuple[str, ...] = (
    "CREATED",
    "PATH_RUNNING",
    "PATH_FOUND",
    "REFINING",
    "TS_FOUND",
    "TS_VALIDATED",
    "IRC_RUNNING",
    "IRC_COMPLETE",
    "ENDPOINTS_VALIDATED",
    "VALIDATED",
)

STEP_FAILED_STATUSES: tuple[str, ...] = (
    "FAILED_PATH",
    "FAILED_REFINEMENT",
    "FAILED_TS_VALIDATION",
    "FAILED_IRC",
    "FAILED_ENDPOINT_VALIDATION",
    "AMBIGUOUS_ENDPOINT",
)


def step_top_status(internal_status: str | None) -> ModuleStatus:
    """Map the engine's internal status to the public ModuleStatus."""
    if internal_status == "VALIDATED":
        return "validated"
    if internal_status == "AMBIGUOUS_ENDPOINT":
        return "waiting_review"
    if internal_status in STEP_FAILED_STATUSES:
        return "failed"
    return "partial"


# ---------------------------------------------------------------------------
# Failure record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureRecord:
    """One failure point along a module run.

    ``stage`` uses the internal phase tag (``path``/``refinement``/
    ``ts_validation``/``irc``/``endpoint_validation``); ``reason`` is a
    machine-readable key; ``details`` carries rescue history etc.
    """

    stage: str
    reason: str
    recoverable: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "reason": self.reason,
            "recoverable": self.recoverable,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureRecord:
        return cls(
            stage=str(data.get("stage") or ""),
            reason=str(data.get("reason") or ""),
            recoverable=bool(data.get("recoverable", True)),
            details=dict(data.get("details") or {}),
        )


# ---------------------------------------------------------------------------
# IRC endpoint resolution (forward/reverse -> source/sink)
# ---------------------------------------------------------------------------

EndpointDirection = Literal["forward", "reverse"]
EndpointRole = Literal["source", "sink", "unknown"]


@dataclass
class ResolvedEndpoint:
    """One IRC endpoint resolved against the source state.

    ``role`` is determined dynamically (connectivity comparison against
    ``source_state``): an endpoint that matches the source side is
    ``"source"``; the far side is ``"sink"``. IRC forward/reverse NEVER
    implies reactant/product.
    """

    endpoint_id: str
    direction: EndpointDirection
    role: EndpointRole
    raw_geometry: ArtifactRef
    optimized_minimum: StationaryPoint | None = None
    minimum_validated: bool = False
    match_verdict: EndpointVerdict = "FAILED"
    matched_state_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "direction": self.direction,
            "role": self.role,
            "raw_geometry": self.raw_geometry.to_dict(),
            "optimized_minimum": (
                self.optimized_minimum.to_dict() if self.optimized_minimum is not None else None
            ),
            "minimum_validated": self.minimum_validated,
            "match_verdict": self.match_verdict,
            "matched_state_id": self.matched_state_id,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResolvedEndpoint:
        raw = data.get("raw_geometry") or {}
        return cls(
            endpoint_id=str(data.get("endpoint_id") or ""),
            direction=cast(EndpointDirection, data.get("direction") or "forward"),
            role=cast(EndpointRole, data.get("role") or "unknown"),
            raw_geometry=ArtifactRef.from_dict(dict(raw)),
            optimized_minimum=(
                StationaryPoint.from_dict(dict(data.get("optimized_minimum") or {}))
                if isinstance(data.get("optimized_minimum"), dict)
                else None
            ),
            minimum_validated=bool(data.get("minimum_validated", False)),
            match_verdict=cast(EndpointVerdict, data.get("match_verdict") or "FAILED"),
            matched_state_id=(
                str(data.get("matched_state_id")) if data.get("matched_state_id") else None
            ),
            evidence=dict(data.get("evidence") or {}),
        )


# ---------------------------------------------------------------------------
# Elementary Step request / manifest
# ---------------------------------------------------------------------------


@dataclass
class ElementaryStepRequest:
    """Reaction hypothesis consumed by the Elementary Step Engine.

    ``target_state`` is optional at the core level: each path strategy
    declares whether it requires a target (rph-reverse) or not
    (guided-scan / direct-ts).
    """

    step_id: str
    source_state: StableState
    coordinate_plan: ReactionCoordinatePlan
    target_state: StableState | None = None
    charge: int = 0
    multiplicity: int = 1
    path_strategy: str = "rph-reverse"
    refinement_fidelity: str = "s3"
    endpoint_method: str = "irc"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "source_state": self.source_state.to_dict(),
            "target_state": self.target_state.to_dict() if self.target_state is not None else None,
            "coordinate_plan": _serialize_coordinate_plan(self.coordinate_plan),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "path_strategy": self.path_strategy,
            "refinement_fidelity": self.refinement_fidelity,
            "endpoint_method": self.endpoint_method,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ElementaryStepRequest:
        source = data.get("source_state")
        plan = data.get("coordinate_plan")
        if not isinstance(source, dict):
            raise ValueError("ElementaryStepRequest: missing source_state")
        if not isinstance(plan, dict):
            raise ValueError("ElementaryStepRequest: missing coordinate_plan")
        return cls(
            step_id=str(data.get("step_id") or ""),
            source_state=StableState.from_dict(dict(source)),
            coordinate_plan=ReactionCoordinatePlan.from_dict(dict(plan)),
            target_state=(
                StableState.from_dict(dict(data.get("target_state") or {}))
                if isinstance(data.get("target_state"), dict)
                else None
            ),
            charge=int(data.get("charge") or 0),
            multiplicity=int(data.get("multiplicity") or 1),
            path_strategy=str(data.get("path_strategy") or "rph-reverse"),
            refinement_fidelity=str(data.get("refinement_fidelity") or "s3"),
            endpoint_method=str(data.get("endpoint_method") or "irc"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class ElementaryStepManifest:
    """Persisted output of one elementary step.

    Carries everything needed to (a) render a validated elementary step in the
    GUI, (b) feed ``mech-confirm --select ts:canonical|endpoint:sink``, and
    (c) let the Study layer register NEW_STATE endpoints.
    """

    step_id: str
    status: ModuleStatus = "partial"
    target_state_id: str | None = None
    furthest_stage: str | None = None
    coordinate_plan: dict[str, Any] = field(default_factory=dict)
    method: dict[str, Any] = field(default_factory=dict)
    path: dict[str, Any] = field(default_factory=dict)
    transition_state: dict[str, Any] | None = None
    irc: dict[str, Any] | None = None
    gates: dict[str, str] = field(default_factory=dict)
    history: dict[str, Any] = field(default_factory=dict)
    failure: FailureRecord | None = None
    suggested_actions: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def is_validated(self) -> bool:
        return self.status == "validated"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "step_id": self.step_id,
            "status": self.status,
            "target_state_id": self.target_state_id,
            "furthest_stage": self.furthest_stage,
            "coordinate_plan": dict(self.coordinate_plan),
            "method": dict(self.method),
            "path": dict(self.path),
            "transition_state": (
                dict(self.transition_state) if self.transition_state is not None else None
            ),
            "irc": dict(self.irc) if self.irc is not None else None,
            "gates": dict(self.gates),
            "history": dict(self.history),
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "suggested_actions": list(self.suggested_actions),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ElementaryStepManifest:
        return cls(
            step_id=str(data.get("step_id") or ""),
            status=cast(ModuleStatus, data.get("status") or "partial"),
            target_state_id=(
                str(data.get("target_state_id")) if data.get("target_state_id") else None
            ),
            furthest_stage=(
                str(data.get("furthest_stage")) if data.get("furthest_stage") else None
            ),
            coordinate_plan=dict(data.get("coordinate_plan") or {}),
            method=dict(data.get("method") or {}),
            path=dict(data.get("path") or {}),
            transition_state=(
                dict(data.get("transition_state") or {})
                if isinstance(data.get("transition_state"), dict)
                else None
            ),
            irc=dict(data.get("irc") or {}) if isinstance(data.get("irc"), dict) else None,
            gates=dict(data.get("gates") or {}),
            history=dict(data.get("history") or {}),
            failure=(
                FailureRecord.from_dict(dict(data.get("failure") or {}))
                if isinstance(data.get("failure"), dict)
                else None
            ),
            suggested_actions=list(data.get("suggested_actions") or []),
            provenance=dict(data.get("provenance") or {}),
        )


# ---------------------------------------------------------------------------
# Generic module manifest
# ---------------------------------------------------------------------------


@dataclass
class ModuleManifest:
    """Generic interchange envelope for any standalone mechanism module."""

    schema_version: int = 1
    phase: ModulePhase = "conformer"
    label: str | None = None
    status: ModuleStatus = "validated"
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    failure: FailureRecord | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "label": self.label,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "provenance": self.provenance,
        }
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "label": self.label,
            "status": self.status,
            "input": dict(self.input),
            "output": dict(self.output),
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "provenance": dict(self.provenance),
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleManifest:
        return cls(
            schema_version=int(data.get("schema_version") or 1),
            phase=cast(ModulePhase, data.get("phase") or "conformer"),
            label=str(data.get("label")) if data.get("label") else None,
            status=cast(ModuleStatus, data.get("status") or "validated"),
            input=dict(data.get("input") or {}),
            output=dict(data.get("output") or {}),
            failure=(
                FailureRecord.from_dict(dict(data.get("failure") or {}))
                if isinstance(data.get("failure"), dict)
                else None
            ),
            provenance=dict(data.get("provenance") or {}),
        )


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

MANIFEST_FILENAME = "module_manifest.json"
ELEMENTARY_STEP_FILENAME = "elementary_step_manifest.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_module_manifest(output_dir: Path, manifest: ModuleManifest) -> Path:
    """Persist a module manifest under *output_dir* (atomic write)."""
    return _write_json_atomic(output_dir / MANIFEST_FILENAME, manifest.to_dict())


def read_module_manifest(manifest_path: Path) -> ModuleManifest:
    return ModuleManifest.from_dict(_read_json(manifest_path))


def write_elementary_step_manifest(output_dir: Path, manifest: ElementaryStepManifest) -> Path:
    return _write_json_atomic(output_dir / ELEMENTARY_STEP_FILENAME, manifest.to_dict())


def read_elementary_step_manifest(manifest_path: Path) -> ElementaryStepManifest:
    return ElementaryStepManifest.from_dict(_read_json(manifest_path))
