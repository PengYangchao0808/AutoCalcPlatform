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

from dataclasses import dataclass
from typing import Literal, Union

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


CoordinateConstraint = Union[DistanceConstraint, AngleConstraint, DihedralConstraint]


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

    def __post_init__(self) -> None:
        expected = _ATOM_COUNTS[self.kind]
        if len(self.atoms) != expected:
            raise ValueError(
                f"CoordinateSpec {self.id!r}: kind={self.kind!r} requires "
                f"{expected} atoms, got {len(self.atoms)}"
            )
        if self.role == "drive" and (self.start is None or self.end is None):
            raise ValueError(
                f"CoordinateSpec {self.id!r}: drive coordinates require "
                "both start and end values"
            )
        if self.role == "freeze" and self.start is None:
            raise ValueError(
                f"CoordinateSpec {self.id!r}: freeze coordinates require a start value"
            )

    def constraint_at(self, progress: float) -> CoordinateConstraint:
        """Return the constraint at synchronous *progress* (0.0 → 1.0).

        ``drive`` → interpolated target; ``freeze`` → pinned at ``start``;
        ``monitor`` → raises (monitor coordinates are never constrained).
        """
        if self.role == "monitor":
            raise ValueError(
                f"CoordinateSpec {self.id!r} is monitor-only; no constraint to build"
            )
        if self.role == "freeze":
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
        if not isinstance(atoms, (list, tuple)) or not all(
            isinstance(a, int) for a in atoms
        ):
            raise ValueError(f"CoordinateSpec: invalid atoms {atoms!r}")
        kind = data.get("kind")
        if kind not in _ATOM_COUNTS:
            raise ValueError(f"CoordinateSpec: invalid kind {kind!r}")
        return cls(
            id=str(data.get("id") or "rc"),
            kind=kind,  # type: ignore[arg-type]
            atoms=tuple(int(a) for a in atoms),
            role=str(data.get("role") or "drive"),  # type: ignore[arg-type]
            start=_opt_float(data.get("start")),
            end=_opt_float(data.get("end")),
            force_constant=_opt_float(data.get("force_constant")),
        )


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

    def __post_init__(self) -> None:
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
            constraints.append(spec.constraint_at(progress))
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
            constraint = spec.constraint_at(progress)
            targets[spec.id] = float(constraint.target)
        return targets

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ReactionCoordinatePlan:
        """Build from a plain JSON-style dict (frontend round-trip)."""
        coords = data.get("coordinates")
        if not isinstance(coords, list):
            raise ValueError("ReactionCoordinatePlan: 'coordinates' must be a list")
        return cls(
            coordinates=tuple(CoordinateSpec.from_dict(c) for c in coords),
            points=int(data.get("points") or 21),
            coupling=str(data.get("coupling") or "synchronous"),  # type: ignore[arg-type]
            start_from=str(data.get("start_from") or "reactant"),  # type: ignore[arg-type]
        )


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "AngleConstraint",
    "ConstraintKind",
    "ConstraintRole",
    "CoordinateConstraint",
    "CoordinateSpec",
    "DihedralConstraint",
    "DistanceConstraint",
    "ReactionCoordinatePlan",
]
