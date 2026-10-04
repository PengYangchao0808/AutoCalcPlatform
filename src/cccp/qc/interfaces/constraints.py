"""
Internal Coordinate Constraints
================================

Generic internal-coordinate primitives for reaction-path driving. Pure data
layer (no subprocess): a :class:`CoordinateSpec` describes one driven/frozen/
monitored internal coordinate, and :class:`ReactionCoordinatePlan` compiles a
list of them into a synchronous multi-coordinate reaction coordinate
(``q_i(λ) = q_i,start + λ·(q_i,end − q_i,start)``).

The user-facing semantics (form bond / break bond / rotate dihedral / bend
angle) are compiled by callers into these primitives; QC engines only ever see
``distance`` / ``angle`` / ``dihedral`` constraints.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from typing import Literal, TypeAlias, cast

ConstraintKind = Literal["distance", "angle", "dihedral"]
ConstraintRole = Literal["drive", "freeze", "monitor"]

_ATOM_COUNTS: dict[str, int] = {"distance": 2, "angle": 3, "dihedral": 4}


@dataclass(frozen=True)
class DistanceConstraint:
    """Constrain an inter-atomic distance (A-B) to *target* Angstrom."""

    atoms: tuple[int, int]
    target: float
    force_constant: float | None = None


@dataclass(frozen=True)
class AngleConstraint:
    """Constrain a bond angle (A-B-C) to *target* degrees."""

    atoms: tuple[int, int, int]
    target: float
    force_constant: float | None = None


@dataclass(frozen=True)
class DihedralConstraint:
    """Constrain a dihedral (A-B-C-D) to *target* degrees."""

    atoms: tuple[int, int, int, int]
    target: float
    force_constant: float | None = None


CoordinateConstraint: TypeAlias = DistanceConstraint | AngleConstraint | DihedralConstraint


@dataclass(frozen=True)
class CoordinateSpec:
    """One internal coordinate in a reaction-coordinate plan.

    Attributes:
        id: Stable identifier used across plan / path / results (e.g. ``"rc1"``).
        kind: Internal-coordinate kind.
        atoms: 0-based atom indices (2 / 3 / 4 atoms per kind).
        role: ``"drive"`` — pushed along start→end; ``"freeze"`` — pinned at
            ``start``; ``"monitor"`` — tracked but never constrained.
        start: Initial value (distance in Å, angles/dihedrals in degrees).
        end: Final value for a ``drive`` coordinate (interpolated).
        force_constant: Optional constraint force constant (engine-specific).
    """

    id: str
    kind: ConstraintKind
    atoms: tuple[int, ...]
    role: ConstraintRole = "drive"
    start: float | None = None
    end: float | None = None
    force_constant: float | None = None
    values: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        expected = _ATOM_COUNTS[self.kind]
        if len(self.atoms) != expected:
            raise ValueError(
                f"CoordinateSpec {self.id!r}: kind={self.kind!r} requires {expected} atoms, "
                + f"got {len(self.atoms)}"
            )
        if self.role == "drive" and (self.start is None or self.end is None):
            raise ValueError(
                f"CoordinateSpec {self.id!r}: drive coordinates require both start and end values"
            )
        if self.role == "freeze" and self.start is None:
            raise ValueError(
                f"CoordinateSpec {self.id!r}: freeze coordinates require a start value"
            )

    def constraint_at(self, progress: float, index: int | None = None) -> CoordinateConstraint:
        """Return the constraint at synchronous *progress* (0.0 → 1.0).

        ``drive`` → interpolated target; ``freeze`` → pinned at ``start``;
        ``monitor`` → raises (monitor coordinates are never constrained).
        """
        if self.role == "monitor":
            raise ValueError(f"CoordinateSpec {self.id!r} is monitor-only; no constraint to build")
        if self.values:
            if index is None:
                raise ValueError("explicit coordinate values require a frame index")
            target = self.values[index]
        elif self.role == "freeze":
            target = self.start
            assert target is not None  # validated in __post_init__
        else:
            assert self.start is not None and self.end is not None
            target = self.start + progress * (self.end - self.start)
        if self.kind == "distance":
            return DistanceConstraint(
                atoms=(self.atoms[0], self.atoms[1]),
                target=float(target),
                force_constant=self.force_constant,
            )
        if self.kind == "angle":
            return AngleConstraint(
                atoms=(self.atoms[0], self.atoms[1], self.atoms[2]),
                target=float(target),
                force_constant=self.force_constant,
            )
        return DihedralConstraint(
            atoms=(
                self.atoms[0],
                self.atoms[1],
                self.atoms[2],
                self.atoms[3],
            ),
            target=float(target),
            force_constant=self.force_constant,
        )

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CoordinateSpec:
        """Build from a plain JSON-style dict (frontend round-trip)."""
        atoms = data.get("atoms")
        raw_atoms = cast(list[object] | tuple[object, ...] | None, atoms)
        if raw_atoms is None or not all(isinstance(atom, int) for atom in raw_atoms):
            raise ValueError(f"CoordinateSpec: invalid atoms {atoms!r}")
        typed_atoms = cast(list[int] | tuple[int, ...], raw_atoms)
        kind = data.get("kind")
        if kind not in _ATOM_COUNTS:
            raise ValueError(f"CoordinateSpec: invalid kind {kind!r}")
        role = data.get("role") or "drive"
        if role not in {"drive", "freeze", "monitor"}:
            raise ValueError(f"CoordinateSpec: invalid role {role!r}")
        return cls(
            id=str(data.get("id") or "rc"),
            kind=cast(ConstraintKind, kind),
            atoms=tuple(int(atom) for atom in typed_atoms),
            role=cast(ConstraintRole, role),
            start=_opt_float(data.get("start")),
            end=_opt_float(data.get("end")),
            force_constant=_opt_float(data.get("force_constant")),
            values=tuple(float(v) for v in data.get("values", ())),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialise to the plain JSON-style dict accepted by :meth:`from_dict`."""
        payload: dict[str, object] = {
            "id": self.id,
            "kind": self.kind,
            "atoms": list(self.atoms),
            "role": self.role,
        }
        if self.start is not None:
            payload["start"] = self.start
        if self.end is not None:
            payload["end"] = self.end
        if self.force_constant is not None:
            payload["force_constant"] = self.force_constant
        if self.values:
            payload["values"] = list(self.values)
        return payload


@dataclass(frozen=True)
class ReactionCoordinatePlan:
    """A synchronous multi-coordinate reaction path.

    All ``drive`` coordinates advance together from their ``start`` to their
    ``end`` values over ``points`` frames (λ = index/(points−1)).

    Attributes:
        coordinates: Internal coordinates (drive / freeze / monitor mix).
        points: Number of scan frames (including both endpoints).
        coupling: Only ``"synchronous"`` is supported (uniform λ scaling).
        start_from: Semantic anchor for the path (reactant / product / custom).
    """

    coordinates: tuple[CoordinateSpec, ...]
    points: int = 21
    coupling: Literal["synchronous"] = "synchronous"
    start_from: Literal["reactant", "product", "custom"] = "reactant"

    lambda_values: tuple[float, ...] = ()
    reference_geometries: tuple = ()
    fixed_endpoints: bool = False
    xtb_scc_max_iterations: int | None = None

    def __post_init__(self) -> None:
        if self.lambda_values and (len(self.lambda_values)!=self.points or self.lambda_values[0]!=0 or self.lambda_values[-1]!=1 or any(not math.isfinite(x) for x in self.lambda_values) or any(b<=a for a,b in zip(self.lambda_values,self.lambda_values[1:]))):
            raise ValueError("lambda_values must increase from zero to one with one value per point")
        if any(c.values and (len(c.values)!=self.points or any(not math.isfinite(v) for v in c.values) or abs(c.values[0]-c.start)>1e-7 or abs(c.values[-1]-c.end)>1e-7) for c in self.coordinates):
            raise ValueError("coordinate values must be finite, match points and endpoints")
        if self.reference_geometries:
            import numpy as np
            geometry = np.asarray(self.reference_geometries, dtype=float)
            if geometry.ndim != 3 or geometry.shape[2] != 3 or not np.isfinite(geometry).all():
                raise ValueError("invalid full reference geometry table")
        if self.reference_geometries and len(self.reference_geometries)!=self.points:
            raise ValueError("one reference geometry is required per point")
        if self.fixed_endpoints and not self.reference_geometries:
            raise ValueError("fixed boundaries require reference geometries")
        if self.points < 2:
            raise ValueError("ReactionCoordinatePlan requires points >= 2")
        if not any(c.role == "drive" for c in self.coordinates):
            raise ValueError("ReactionCoordinatePlan requires at least one drive coordinate")

    def drive_coordinates(self) -> tuple[CoordinateSpec, ...]:
        return tuple(c for c in self.coordinates if c.role == "drive")

    def freeze_coordinates(self) -> tuple[CoordinateSpec, ...]:
        return tuple(c for c in self.coordinates if c.role == "freeze")

    def monitor_coordinates(self) -> tuple[CoordinateSpec, ...]:
        return tuple(c for c in self.coordinates if c.role == "monitor")

    def frame_constraints(self, index: int) -> tuple[CoordinateConstraint, ...]:
        """Constraints active at frame *index* (drive + freeze coordinates)."""
        if not 0 <= index < self.points:
            raise IndexError(f"frame index {index} out of range [0, {self.points})")
        progress = index / (self.points - 1)
        constraints: list[CoordinateConstraint] = []
        for spec in self.coordinates:
            if spec.role == "monitor":
                continue
            constraints.append(spec.constraint_at(progress, index))
        return tuple(constraints)

    def coordinate_targets(self, index: int) -> dict[str, float]:
        """Coordinate values at frame *index*: drive = interpolated, freeze = start."""
        if not 0 <= index < self.points:
            raise IndexError(f"frame index {index} out of range [0, {self.points})")
        progress = index / (self.points - 1)
        targets: dict[str, float] = {}
        for spec in self.coordinates:
            if spec.role == "monitor":
                continue
            constraint = spec.constraint_at(progress, index)
            targets[spec.id] = float(constraint.target)
        return targets

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ReactionCoordinatePlan:
        """Build from a plain JSON-style dict (frontend round-trip)."""
        coords = data.get("coordinates")
        raw_coords = cast(list[object] | None, coords)
        if raw_coords is None:
            raise ValueError("ReactionCoordinatePlan: 'coordinates' must be a list")
        if not all(isinstance(coord, dict) for coord in raw_coords):
            raise ValueError("ReactionCoordinatePlan: 'coordinates' entries must be dicts")
        typed_coords = cast(list[dict[str, object]], raw_coords)
        coupling = data.get("coupling") or "synchronous"
        if coupling != "synchronous":
            raise ValueError(f"ReactionCoordinatePlan: unsupported coupling {coupling!r}")
        start_from = data.get("start_from") or "reactant"
        if start_from not in {"reactant", "product", "custom"}:
            raise ValueError(f"ReactionCoordinatePlan: invalid start_from {start_from!r}")
        # Optional full-fidelity fields (path_plan extras).  Absent keys keep
        # the pre-extension defaults so legacy payloads parse unchanged.
        raw_lambda = data.get("lambda_values") or ()
        lambda_values = tuple(float(value) for value in cast("list[object]", raw_lambda))
        raw_refs = data.get("reference_geometries") or ()
        reference_geometries = tuple(cast("list[object]", raw_refs))
        fixed_endpoints = bool(data.get("fixed_endpoints") or False)
        raw_xtb_iters = data.get("xtb_scc_max_iterations")
        xtb_scc_max_iterations = (
            None if raw_xtb_iters is None else int(raw_xtb_iters)  # type: ignore[arg-type]
        )
        return cls(
            coordinates=tuple(CoordinateSpec.from_dict(coord) for coord in typed_coords),
            points=_opt_int(data.get("points"), default=21),
            coupling=cast(Literal["synchronous"], coupling),
            start_from=cast(Literal["reactant", "product", "custom"], start_from),
            lambda_values=lambda_values,
            reference_geometries=reference_geometries,
            fixed_endpoints=fixed_endpoints,
            xtb_scc_max_iterations=xtb_scc_max_iterations,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialise to the plain JSON-style dict accepted by :meth:`from_dict`.

        The full-fidelity fields are emitted only when non-default so
        legacy round-trip payloads keep their exact shape.
        """
        payload: dict[str, object] = {
            "coordinates": [coordinate.to_dict() for coordinate in self.coordinates],
            "points": self.points,
            "coupling": self.coupling,
            "start_from": self.start_from,
        }
        if self.lambda_values:
            payload["lambda_values"] = [float(value) for value in self.lambda_values]
        if self.reference_geometries:
            payload["reference_geometries"] = [
                [[float(c) for c in row] for row in geometry]
                for geometry in self.reference_geometries
            ]
        if self.fixed_endpoints:
            payload["fixed_endpoints"] = True
        if self.xtb_scc_max_iterations is not None:
            payload["xtb_scc_max_iterations"] = int(self.xtb_scc_max_iterations)
        return payload


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def orca_constraint_block(constraints: Sequence[CoordinateConstraint]) -> str:
    """Render an ORCA ``Constraints`` sub-block for *constraints*.

    ORCA ``%geom`` constraint/scan indices are **0-based** (manual:
    ``{ B 0 1 1.25 C }``), so atoms are written verbatim without any +1
    conversion — a +1 here silently constrains the neighbouring atoms.
    Note the contrast with the xTB xcontrol writer
    (:func:`cccp.qc.interfaces.xtb_scan.xcontrol_constraint_block`),
    which is 1-based per the xTB convention.

    Per-line syntax: ``{ B i j value C }`` / ``{ A i j k value C }`` /
    ``{ D i j k l value C }`` — the target value precedes the trailing
    ``C`` flag.
    """
    if not constraints:
        return ""
    lines = ["Constraints"]
    for constraint in constraints:
        if isinstance(constraint, DistanceConstraint):
            kind = "B"
        elif isinstance(constraint, AngleConstraint):
            kind = "A"
        else:
            kind = "D"
        atoms = " ".join(str(int(atom)) for atom in constraint.atoms)
        lines.append(f"  {{ {kind} {atoms} {constraint.target:.8f} C }}")
    lines.append("end")
    return "\n".join(lines)


__all__ = [
    "AngleConstraint",
    "ConstraintKind",
    "ConstraintRole",
    "CoordinateConstraint",
    "CoordinateSpec",
    "DihedralConstraint",
    "DistanceConstraint",
    "orca_constraint_block",
    "ReactionCoordinatePlan",
]
