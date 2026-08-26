"""S2 bond-length-scan data contracts (docs/ACP_S2_Bond_Length_Scan_MD_Plan.md §6–§9).

Pure dataclass models — no IO, no QC coupling.  Every model is frozen and
serialises losslessly through :meth:`to_dict` / :meth:`from_dict` so the
request can travel CLI → scheduler → workflow as plain JSON without losing
the plan's field semantics.

Coordinate/atom indices are **0-based** everywhere (plan §5.1); 1-based
display conversion is a presentation concern handled at the API/frontend
boundary.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SCAN_PROTOCOL_NAME",
    "MIN_SCAN_POINTS",
    "MAX_SCAN_POINTS",
    "MIN_SCAN_STEP_ANGSTROM",
    "BondLengthScanRequest",
    "CANDIDATE_ROLES",
    "CandidateRecommendation",
    "EnergyProfile",
    "ReviewCandidate",
    "ScanCoordinate",
    "ScanDriver",
    "ScanFrame",
    "ScanOptimizer",
    "ScanProtocol",
    "ScanQuality",
    "ScanReview",
    "SinglePointSpec",
    "StructureSelector",
    "StructureSource",
    "build_default_protocol",
    "coordinate_step_angstrom",
    "normalize_candidate_role",
    "suggestion_presets",
    "validate_scan_protocol",
]


def _coerce_int(value: Any, default: int) -> int:
    return default if value is None else int(value)


MIN_SCAN_POINTS = 3
MAX_SCAN_POINTS = 101
MIN_SCAN_STEP_ANGSTROM = 0.01
MIN_SCAN_STEP_DEGREE = 0.1
DEFAULT_SCAN_PROTOCOL_NAME = "orca_relaxed_scan_xtb_gfn2_sp_b973c_v1"

SourceType = Literal["task_artifact", "structure_asset", "xyz_text"]


@dataclass(frozen=True)
class StructureSelector:
    """Structure picker for a ``task_artifact`` source (plan §4.1)."""

    kind: str = "final_structure"  # "final_structure" | "frame_index"
    frame_index: int | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> StructureSelector:
        payload = dict(payload or {})
        kind = str(payload.get("kind") or "final_structure")
        frame_raw = payload.get("frame_index")
        frame_index = None if frame_raw is None else _coerce_int(frame_raw, 0)
        return cls(kind=kind, frame_index=frame_index)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "frame_index": self.frame_index}


@dataclass(frozen=True)
class StructureSource:
    """One of the three structure sources (plan §4.1)."""

    source_type: str = "xyz_text"
    source_job_id: str | None = None
    artifact_path: str | None = None
    structure_selector: StructureSelector = field(default_factory=StructureSelector)
    asset_id: str | None = None
    asset_path: str | None = None
    xyz_text: str | None = None
    charge: int = 0
    multiplicity: int = 1

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> StructureSource:
        payload = dict(payload or {})
        source_type = str(payload.get("source_type") or "xyz_text")
        if source_type not in ("task_artifact", "structure_asset", "xyz_text"):
            raise ValueError(f"Unknown source_type: {source_type!r}")
        return cls(
            source_type=source_type,
            source_job_id=(
                None if payload.get("source_job_id") is None else str(payload["source_job_id"])
            ),
            artifact_path=(
                None if payload.get("artifact_path") is None else str(payload["artifact_path"])
            ),
            structure_selector=StructureSelector.from_dict(
                dict(payload.get("structure_selector") or {})
            ),
            asset_id=None if payload.get("asset_id") is None else str(payload["asset_id"]),
            asset_path=None if payload.get("asset_path") is None else str(payload["asset_path"]),
            xyz_text=None if payload.get("xyz_text") is None else str(payload["xyz_text"]),
            charge=_coerce_int(payload.get("charge"), 0),
            multiplicity=_coerce_int(payload.get("multiplicity"), 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_job_id": self.source_job_id,
            "artifact_path": self.artifact_path,
            "structure_selector": self.structure_selector.to_dict(),
            "asset_id": self.asset_id,
            "asset_path": self.asset_path,
            "xyz_text": self.xyz_text,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }


@dataclass(frozen=True)
class ScanCoordinate:
    """Driven coordinate for a one-dimensional relaxed scan.

    Atoms are 0-based.  Distances use Angstrom and angles/dihedrals use
    degrees.  ``bond_type`` and ``intent`` are presentation/provenance
    metadata; the actual direction is always the explicit ``start → end``
    range.
    """

    kind: str = "distance"
    atoms: tuple[int, ...] = (0, 1)
    unit: str = "angstrom"
    start: float | None = None
    end: float | None = None
    n_points: int = 16
    bond_type: str | None = None
    direction: str | None = None
    intent: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ScanCoordinate:
        payload = dict(payload or {})
        raw_atoms = payload.get("atoms")
        kind = str(payload.get("kind") or "distance")
        expected_atoms = {"distance": 2, "angle": 3, "dihedral": 4}.get(kind)
        if expected_atoms is None:
            raise ValueError("coordinate.kind must be one of 'distance', 'angle', or 'dihedral'")
        if not isinstance(raw_atoms, (list, tuple)) or len(raw_atoms) != expected_atoms:
            raise ValueError(f"coordinate.kind='{kind}' requires {expected_atoms} atoms")
        atoms = (int(raw_atoms[0]), int(raw_atoms[1]))
        if expected_atoms > 2:
            atoms = tuple(int(atom) for atom in raw_atoms)
        start_raw = payload.get("start")
        end_raw = payload.get("end")
        n_points = _coerce_int(payload.get("n_points"), 16)
        direction = None if payload.get("direction") is None else str(payload["direction"])
        return cls(
            kind=kind,
            atoms=atoms,
            unit=str(payload.get("unit") or ("angstrom" if kind == "distance" else "degree")),
            start=None if start_raw is None else float(start_raw),
            end=None if end_raw is None else float(end_raw),
            n_points=n_points,
            bond_type=(None if payload.get("bond_type") is None else str(payload["bond_type"])),
            direction=direction,
            intent=None if payload.get("intent") is None else str(payload["intent"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "atoms": list(self.atoms),
            "unit": self.unit,
            "start": self.start,
            "end": self.end,
            "n_points": self.n_points,
            "bond_type": self.bond_type,
            "direction": self.direction,
            "intent": self.intent,
        }


@dataclass(frozen=True)
class ScanDriver:
    """Scan-driving layer: constrained scan + per-point geometry optimisation."""

    software: str = "orca"
    mode: str = "relaxed_scan"
    reuse_previous_geometry: bool = True
    full_scan: bool = True
    use_scants: bool = False
    max_iterations: int = 250
    failure_policy: str = "retry_previous"
    retry_count: int = 2

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ScanDriver:
        payload = dict(payload or {})
        return cls(
            software=str(payload.get("software") or "orca"),
            mode=str(payload.get("mode") or "relaxed_scan"),
            reuse_previous_geometry=bool(payload.get("reuse_previous_geometry", True)),
            full_scan=bool(payload.get("full_scan", True)),
            use_scants=bool(payload.get("use_scants", False)),
            max_iterations=_coerce_int(payload.get("max_iterations"), 250),
            failure_policy=str(payload.get("failure_policy") or "retry_previous"),
            retry_count=_coerce_int(payload.get("retry_count"), 2),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "software": self.software,
            "mode": self.mode,
            "reuse_previous_geometry": self.reuse_previous_geometry,
            "full_scan": self.full_scan,
            "use_scants": self.use_scants,
            "max_iterations": self.max_iterations,
            "failure_policy": self.failure_policy,
            "retry_count": self.retry_count,
        }


@dataclass(frozen=True)
class ScanOptimizer:
    """Per-point optimisation level used inside the scan driver."""

    method: str = "GFN2-xTB"
    max_iterations: int = 250
    convergence: str = "normal"
    retry_count: int = 2
    retry_strategy: str = "previous_geometry"

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ScanOptimizer:
        payload = dict(payload or {})
        return cls(
            method=str(payload.get("method") or "GFN2-xTB"),
            max_iterations=_coerce_int(payload.get("max_iterations"), 250),
            convergence=str(payload.get("convergence") or "normal"),
            retry_count=_coerce_int(payload.get("retry_count"), 2),
            retry_strategy=str(payload.get("retry_strategy") or "previous_geometry"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "max_iterations": self.max_iterations,
            "convergence": self.convergence,
            "retry_count": self.retry_count,
            "retry_strategy": self.retry_strategy,
        }


@dataclass(frozen=True)
class SinglePointSpec:
    """Post-scan single-point energy refinement layer (plan §6.1)."""

    enabled: bool = True
    software: str = "orca"
    method: str = "B97-3c"
    basis: str | None = None
    dispersion: str | None = None
    ri_approximation: str = "none"
    aux_j_basis: str | None = None
    aux_c_basis: str | None = None
    solvent_model: str = "none"
    solvent: str | None = None
    grid: str | None = None
    scf_convergence: str | None = None
    charge: int = 0
    multiplicity: int = 1
    resume: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> SinglePointSpec:
        payload = dict(payload or {})
        return cls(
            enabled=bool(payload.get("enabled", True)),
            software=str(payload.get("software") or "orca"),
            method=str(payload.get("method") or "B97-3c"),
            basis=None if payload.get("basis") is None else str(payload["basis"]),
            dispersion=(None if payload.get("dispersion") is None else str(payload["dispersion"])),
            ri_approximation=str(payload.get("ri_approximation") or "none"),
            aux_j_basis=(
                None if payload.get("aux_j_basis") is None else str(payload["aux_j_basis"])
            ),
            aux_c_basis=(
                None if payload.get("aux_c_basis") is None else str(payload["aux_c_basis"])
            ),
            solvent_model=str(payload.get("solvent_model") or "none"),
            solvent=None if payload.get("solvent") is None else str(payload["solvent"]),
            grid=None if payload.get("grid") is None else str(payload["grid"]),
            scf_convergence=(
                None if payload.get("scf_convergence") is None else str(payload["scf_convergence"])
            ),
            charge=_coerce_int(payload.get("charge"), 0),
            multiplicity=_coerce_int(payload.get("multiplicity"), 1),
            resume=bool(payload.get("resume", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "software": self.software,
            "method": self.method,
            "basis": self.basis,
            "dispersion": self.dispersion,
            "ri_approximation": self.ri_approximation,
            "aux_j_basis": self.aux_j_basis,
            "aux_c_basis": self.aux_c_basis,
            "solvent_model": self.solvent_model,
            "solvent": self.solvent,
            "grid": self.grid,
            "scf_convergence": self.scf_convergence,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "resume": self.resume,
        }


@dataclass(frozen=True)
class ScanProtocol:
    """Full scan protocol — the three computation layers stay separate (§6.1)."""

    scan_type: str = "bond_length"
    coordinate: ScanCoordinate = field(default_factory=ScanCoordinate)
    scan_driver: ScanDriver = field(default_factory=ScanDriver)
    scan_optimizer: ScanOptimizer = field(default_factory=ScanOptimizer)
    single_point: SinglePointSpec = field(default_factory=SinglePointSpec)
    suggestion_source: str | None = None
    name: str = DEFAULT_SCAN_PROTOCOL_NAME

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ScanProtocol:
        payload = dict(payload or {})
        coordinate = ScanCoordinate.from_dict(dict(payload.get("coordinate") or {}))
        driver_payload = dict(payload.get("scan_driver") or {})
        optimizer_payload = dict(payload.get("scan_optimizer") or {})
        sp_payload = dict(payload.get("single_point") or {})
        # Accept the flat legacy shape (scan_software/scan_method/...) for §7.1.
        if not driver_payload and payload.get("scan_software"):
            driver_payload = {
                "software": payload.get("scan_software"),
                "mode": "relaxed_scan",
                "use_scants": False,
            }
        if not optimizer_payload and payload.get("scan_method"):
            optimizer_payload = {"method": payload.get("scan_method")}
        if not sp_payload and payload.get("single_point_enabled") is not None:
            sp_payload = {
                "enabled": bool(payload.get("single_point_enabled")),
                "software": payload.get("single_point_software"),
                "method": payload.get("single_point_method"),
                "basis": payload.get("single_point_basis"),
            }
        driver = ScanDriver.from_dict(driver_payload)
        optimizer = ScanOptimizer.from_dict(optimizer_payload)
        single_point = SinglePointSpec.from_dict(sp_payload)
        return cls(
            scan_type=str(payload.get("scan_type") or _scan_type_for_kind(coordinate.kind)),
            coordinate=coordinate,
            scan_driver=driver,
            scan_optimizer=optimizer,
            single_point=single_point,
            suggestion_source=(
                None
                if payload.get("suggestion_source") is None
                else str(payload["suggestion_source"])
            ),
            name=str(payload.get("name") or DEFAULT_SCAN_PROTOCOL_NAME),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_type": self.scan_type,
            "coordinate": self.coordinate.to_dict(),
            "scan_driver": self.scan_driver.to_dict(),
            "scan_optimizer": self.scan_optimizer.to_dict(),
            "single_point": self.single_point.to_dict(),
            "suggestion_source": self.suggestion_source,
            "name": self.name,
        }


@dataclass(frozen=True)
class BondLengthScanRequest:
    """Top-level scan request (plan §7.1)."""

    mode: str = "bond_length_scan"
    source: StructureSource = field(default_factory=StructureSource)
    coordinate: ScanCoordinate = field(default_factory=ScanCoordinate)
    protocol: ScanProtocol = field(default_factory=ScanProtocol)
    study_id: str | None = None
    resources: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> BondLengthScanRequest:
        payload = dict(payload or {})
        coordinate = ScanCoordinate.from_dict(dict(payload.get("coordinate") or {}))
        protocol_payload = dict(payload.get("protocol") or {})
        # The driven coordinate belongs to the top-level request.  Older
        # clients duplicated it inside ``protocol`` and could omit ``atoms``
        # there, which made an otherwise valid submission fail with an
        # uncaught ValueError (HTTP 500).  Always canonicalise the protocol
        # copy from the validated top-level coordinate instead of trusting a
        # stale/incomplete duplicate.
        protocol_payload["coordinate"] = coordinate.to_dict()
        return cls(
            mode=str(payload.get("mode") or "bond_length_scan"),
            source=StructureSource.from_dict(dict(payload.get("source") or {})),
            coordinate=coordinate,
            protocol=ScanProtocol.from_dict(protocol_payload),
            study_id=None if payload.get("study_id") is None else str(payload["study_id"]),
            resources=dict(payload.get("resources") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source.to_dict(),
            "coordinate": self.coordinate.to_dict(),
            "protocol": self.protocol.to_dict(),
            "study_id": self.study_id,
            "resources": dict(self.resources),
        }


@dataclass(frozen=True)
class ScanFrame:
    """One extracted scan point (plan §7.2)."""

    index: int
    target_coordinate: float
    actual_coordinate: float
    coordinate_unit: str = "angstrom"
    geometry_path: str = ""
    scan_energy_hartree: float | None = None
    single_point_energy_hartree: float | None = None
    optimization_converged: bool = True
    single_point_status: str = "skipped"  # completed | failed | skipped | pending
    source_log: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "target_coordinate": self.target_coordinate,
            "actual_coordinate": self.actual_coordinate,
            "coordinate_unit": self.coordinate_unit,
            "geometry_path": self.geometry_path,
            "scan_energy_hartree": self.scan_energy_hartree,
            "single_point_energy_hartree": self.single_point_energy_hartree,
            "optimization_converged": self.optimization_converged,
            "single_point_status": self.single_point_status,
            "source_log": self.source_log,
        }


@dataclass(frozen=True)
class CandidateRecommendation:
    """TS / INT initial-guess recommendation (plan §9.3/§9.4)."""

    candidate_id: str
    kind: str  # "ts" | "intermediate"
    frame_index: int
    geometry_path: str
    score: float
    confidence: str  # high | medium | low
    evidence: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "frame_index": self.frame_index,
            "geometry_path": self.geometry_path,
            "score": self.score,
            "confidence": self.confidence,
            "evidence": dict(self.evidence),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EnergyProfile:
    """Energy curve data (plan §9.1)."""

    energy_source: str  # single_point | scan | mixed
    unit: str  # kcal/mol
    reference_index: int = 0
    relative_energies_kcal_mol: tuple[float | None, ...] = ()
    raw_hartree: tuple[float | None, ...] = ()
    sp_incomplete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "energy_source": self.energy_source,
            "unit": self.unit,
            "reference_index": self.reference_index,
            "relative_energies_kcal_mol": list(self.relative_energies_kcal_mol),
            "raw_hartree": list(self.raw_hartree),
            "sp_incomplete": self.sp_incomplete,
        }


CANDIDATE_ROLES: tuple[str, ...] = ("ts", "intermediate")


def normalize_candidate_role(role: Any) -> str:
    """Normalise a candidate role to ``ts`` / ``intermediate``.

    Accepts the common aliases (``int``, ``transition_state``) and raises
    :class:`ValueError` for anything else.
    """
    normalized = str(role or "").strip().lower()
    aliases = {
        "ts": "ts",
        "transition_state": "ts",
        "intermediate": "intermediate",
        "int": "intermediate",
    }
    if normalized not in aliases:
        raise ValueError(f"candidate role must be one of {CANDIDATE_ROLES} (got {role!r})")
    return aliases[normalized]


@dataclass(frozen=True)
class ReviewCandidate:
    """One user marking over a scan frame (candidate-manifest contract).

    ``candidate_id`` is stable across saves: algorithm ids (``ts_guess_001``)
    keep their recommendation lineage, manual candidates get
    ``manual_frame_NNN`` ids generated by the backend.  An empty id marks a
    not-yet-named manual candidate and is filled in at materialization.
    """

    candidate_id: str = ""
    frame_index: int = 0
    role: str = "ts"  # "ts" | "intermediate" (normalized)
    name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", normalize_candidate_role(self.role))
        object.__setattr__(self, "frame_index", int(self.frame_index))

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ReviewCandidate:
        payload = dict(payload or {})
        candidate_id = str(payload.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("review candidate requires candidate_id")
        return cls(
            candidate_id=candidate_id,
            frame_index=_coerce_int(payload.get("frame_index"), 0),
            role=normalize_candidate_role(payload.get("role")),
            name=None if payload.get("name") is None else str(payload["name"]),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "candidate_id": self.candidate_id,
            "frame_index": self.frame_index,
            "role": self.role,
        }
        if self.name is not None:
            payload["name"] = self.name
        return payload


@dataclass(frozen=True)
class ScanReview:
    """User review gate over the recommended candidates (plan §7.3/§8.2).

    The editable-candidate contract (s2_candidate_v1) stores the user's
    current marking in ``candidates``; the legacy ``selected_ts`` /
    ``selected_intermediates`` id lists remain readable (and are derived
    on write) so older readers keep working.
    """

    required: bool = True
    status: str = "pending"  # pending | confirmed | rejected
    selected_ts: tuple[str, ...] = ()
    selected_intermediates: tuple[str, ...] = ()
    candidates: tuple[ReviewCandidate, ...] = ()
    decided_at: str | None = None
    note: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> ScanReview:
        payload = dict(payload or {})
        raw_candidates = payload.get("candidates") or []
        candidates: list[ReviewCandidate] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            try:
                candidates.append(ReviewCandidate.from_dict(item))
            except ValueError:
                logger.debug("skipping malformed review candidate: %r", item)
        return cls(
            required=bool(payload.get("required", True)),
            status=str(payload.get("status") or "pending"),
            selected_ts=tuple(str(item) for item in (payload.get("selected_ts") or ())),
            selected_intermediates=tuple(
                str(item) for item in (payload.get("selected_intermediates") or ())
            ),
            candidates=tuple(candidates),
            decided_at=(None if payload.get("decided_at") is None else str(payload["decided_at"])),
            note=None if payload.get("note") is None else str(payload["note"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "status": self.status,
            "selected_ts": list(self.selected_ts),
            "selected_intermediates": list(self.selected_intermediates),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "decided_at": self.decided_at,
            "note": self.note,
        }

    def active_candidate_ids(self) -> tuple[str, ...]:
        """Ids of all currently-marked candidates (S3 input contract)."""
        return tuple(candidate.candidate_id for candidate in self.candidates)


@dataclass(frozen=True)
class ScanQuality:
    """Scan result quality flags (plan §8.3/§9.5)."""

    status: str = "ready_for_review"  # ready_for_review | partial | needs_review | failed
    scan_complete: bool = True
    sp_incomplete: bool = False
    needs_review: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "scan_complete": self.scan_complete,
            "sp_incomplete": self.sp_incomplete,
            "needs_review": self.needs_review,
            "notes": list(self.notes),
        }


# ── presets & validation ────────────────────────────────────────────────

suggestion_presets: dict[str, dict[str, Any]] = {
    "bond_forming": {"description": "成键方向扫描", "direction": "descending"},
    "bond_breaking": {"description": "断键方向扫描", "direction": "ascending"},
    "single_bond": {"description": "已知单键拉伸", "direction": None},
    "double_bond": {"description": "已知双键拉伸", "direction": None},
    "custom": {"description": "用户自定义", "direction": None},
}


def coordinate_step_angstrom(coordinate: ScanCoordinate) -> float | None:
    """Return the absolute coordinate step, preserving the legacy name."""
    if coordinate.start is None or coordinate.end is None:
        return None
    if coordinate.n_points <= 1:
        return None
    return abs(float(coordinate.end) - float(coordinate.start)) / (coordinate.n_points - 1)


def _scan_type_for_kind(kind: str) -> str:
    return {
        "distance": "bond_length",
        "angle": "bond_angle",
        "dihedral": "dihedral",
    }.get(kind, "coordinate_scan")


def build_default_protocol(coordinate: ScanCoordinate) -> ScanProtocol:
    """Default protocol: ORCA relaxed scan, GFN2-xTB opt, B97-3c SP."""
    return ScanProtocol(
        scan_type=_scan_type_for_kind(coordinate.kind),
        coordinate=coordinate,
        scan_driver=ScanDriver(software="orca", mode="relaxed_scan", full_scan=True),
        scan_optimizer=ScanOptimizer(method="GFN2-xTB", max_iterations=250),
        single_point=SinglePointSpec(enabled=True, software="orca", method="B97-3c"),
        suggestion_source=None,
        name=DEFAULT_SCAN_PROTOCOL_NAME,
    )


def validate_scan_protocol(
    coordinate: ScanCoordinate,
    protocol: ScanProtocol | None = None,
    *,
    allow_over_point_limit: bool = False,
) -> None:
    """Validate the plan §5.3 rules; raise :class:`ValueError` on violations.

    Args:
        coordinate: The driven scan coordinate.
        protocol: Optional protocol (used to surface SP config).
        allow_over_point_limit: Permit n_points > MAX_SCAN_POINTS after an
            explicit double confirmation (plan §5.3 — UI must confirm first).
    """
    expected_atoms = {"distance": 2, "angle": 3, "dihedral": 4}.get(coordinate.kind)
    if expected_atoms is None:
        raise ValueError(
            "scan coordinate kind must be 'distance', 'angle', or 'dihedral', "
            f"got {coordinate.kind!r}"
        )
    if len(coordinate.atoms) != expected_atoms:
        raise ValueError(f"{coordinate.kind} coordinates require {expected_atoms} atoms")
    if len(set(coordinate.atoms)) != len(coordinate.atoms):
        raise ValueError("scan coordinate atoms must be different atoms")
    expected_unit = "angstrom" if coordinate.kind == "distance" else "degree"
    accepted_units = (
        expected_unit,
        "angstrom" if expected_unit == "angstrom" else "deg",
        "Å",
        "°",
    )
    if coordinate.unit not in accepted_units:
        raise ValueError(f"{coordinate.kind} coordinates must use {expected_unit}")
    if coordinate.start is None or coordinate.end is None:
        raise ValueError("coordinate.start and coordinate.end are required")
    if coordinate.kind == "distance" and (coordinate.start <= 0 or coordinate.end <= 0):
        raise ValueError("start and end distances must be greater than 0")
    if coordinate.kind == "angle" and not (
        0.0 <= coordinate.start <= 180.0 and 0.0 <= coordinate.end <= 180.0
    ):
        raise ValueError("angle scan start and end must be between 0 and 180 degrees")
    if coordinate.kind == "dihedral" and not (
        -360.0 <= coordinate.start <= 360.0 and -360.0 <= coordinate.end <= 360.0
    ):
        raise ValueError("dihedral scan start and end must be between -360 and 360 degrees")
    if math.isclose(float(coordinate.start), float(coordinate.end), abs_tol=1.0e-9):
        raise ValueError("start and end distances must differ")
    if coordinate.n_points < MIN_SCAN_POINTS:
        raise ValueError(f"n_points must be >= {MIN_SCAN_POINTS}")
    if not allow_over_point_limit and coordinate.n_points > MAX_SCAN_POINTS:
        raise ValueError(
            f"n_points exceeds the {MAX_SCAN_POINTS}-point limit; "
            "an explicit double confirmation is required to exceed it"
        )
    step = coordinate_step_angstrom(coordinate)
    minimum_step = MIN_SCAN_STEP_ANGSTROM if coordinate.kind == "distance" else MIN_SCAN_STEP_DEGREE
    step_unit = "Å" if coordinate.kind == "distance" else "°"
    if step is None or step < minimum_step:
        raise ValueError(
            f"scan step must be >= {minimum_step} {step_unit} "
            f"(got {step if step is not None else 'undefined'})"
        )
    if protocol is not None:
        driver = protocol.scan_driver
        if driver.failure_policy not in {
            "retry_previous",
            "retry_original",
            "mark_failed_continue",
            "abort",
        }:
            raise ValueError(f"unknown scan failure policy: {driver.failure_policy!r}")
        if driver.retry_count < 0 or driver.retry_count > 5:
            raise ValueError("scan point retry_count must be between 0 and 5")

        optimizer = protocol.scan_optimizer
        if optimizer.convergence not in {"normal", "tight", "very_tight"}:
            raise ValueError(f"unknown scan optimizer convergence: {optimizer.convergence!r}")
        if optimizer.max_iterations < 1:
            raise ValueError("scan optimizer max_iterations must be greater than 0")
        if optimizer.retry_count < 0 or optimizer.retry_count > 5:
            raise ValueError("scan optimizer retry_count must be between 0 and 5")
        if optimizer.retry_strategy not in {
            "previous_geometry",
            "original_geometry",
            "looser_convergence",
        }:
            raise ValueError(f"unknown scan optimizer retry strategy: {optimizer.retry_strategy!r}")

        if protocol.single_point.enabled:
            sp = protocol.single_point
            if sp.charge is None or sp.multiplicity is None:
                raise ValueError("single_point charge and multiplicity are required")
            if not sp.method:
                raise ValueError("single_point method is required when single_point is enabled")
