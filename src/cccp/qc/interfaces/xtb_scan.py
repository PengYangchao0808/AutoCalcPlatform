"""
xTB Relaxed-Scan Helpers
========================

Pure helpers for driving xTB constrained optimization along a reaction
coordinate: xcontrol ``$constrain`` block generation (1-based atom indices,
xTB convention), the ``$constrain`` syntax contract (typed refusals for
directives xTB would silently ignore), and the relaxed-scan result containers.

Author: QCcalc Team
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from numpy.typing import NDArray

from cccp.qc.interfaces.constraints import (
    AngleConstraint,
    CoordinateConstraint,
    CoordinateSpec,
    DihedralConstraint,
    DistanceConstraint,
    ReactionCoordinatePlan,
)

# ── xcontrol ``$constrain`` syntax contract (round-7 #7a) ──────────────────
#
# xTB is forgiving in the worst way: a directive with the wrong number of
# fields (``distance: 3, 4, 1.95, auto``) only prints
# "not enough arguments to constrain a distance" and then optimizes WITHOUT
# the constraint, exiting 0.  The task layer would report COMPLETED for an
# unconstrained geometry.  These helpers are the pure syntax contract: the
# typed builder validates before rendering, and the submit path can validate
# raw user ``.xcontrol`` text through :func:`validate_xcontrol_text`.

#: Required number of atom indices per constraint kind.
_CONSTRAINT_ATOM_COUNTS: dict[str, int] = {"distance": 2, "angle": 3, "dihedral": 4}

_CONSTRAINT_LINE_RE = re.compile(r"^\s*(?P<kind>[A-Za-z]+)\s*:\s*(?P<body>.*?)\s*$")
_FORCE_CONSTANT_LINE_RE = re.compile(
    r"^\s*force\s+constant\s*=\s*(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$",
    re.IGNORECASE,
)

#: xTB's own wording when it refuses a directive (the run still exits 0).
_CONSTRAINT_REJECTION_RE = re.compile(
    r"(?i)(?:"
    r"not enough arguments to constrain|"
    r"too many arguments to constrain|"
    r"could not (?:read|parse|apply) (?:the )?constrain\w*|"
    r"unable to (?:read|parse|apply) (?:the )?constrain\w*|"
    r"unknown constrain\w*|"
    r"invalid constrain\w*|"
    r"constrain\w*[^\n]{0,80}(?:ignored|not honoured|not honored|not applied|"
    r"will be ignored|is ignored)|"
    r"(?:warning|error)[^\n]{0,120}(?:ignor\w*|drop\w*|skip\w*)[^\n]{0,80}constrain\w*"
    r")"
)


class XtbConstraintSyntaxError(ValueError):
    """A ``$constrain`` directive xTB would silently ignore (typed refusal).

    ``params`` is shaped like the application error envelope so the submit
    path can forward it: ``rule="constraint_syntax_invalid"``,
    ``field="constraints"`` plus the offending line and the expected shape.
    """

    rule = "constraint_syntax_invalid"

    def __init__(
        self,
        message: str,
        *,
        line: str = "",
        field_name: str = "constraints",
        rule: str | None = None,
    ) -> None:
        super().__init__(message)
        self.line = str(line)
        self.field_name = field_name
        self.rule = rule or type(self).rule
        self.params: dict[str, Any] = {
            "rule": self.rule,
            "field": self.field_name,
        }
        if self.line:
            self.params["line"] = self.line


def _expected_fields(kind: str) -> int:
    return _CONSTRAINT_ATOM_COUNTS[kind] + 1


def _parse_constraint_line(kind: str, body: str, line: str) -> dict[str, Any]:
    """Parse one ``kind: i, j[, k[, l]], value`` directive or raise typed error."""
    expected_atoms = _CONSTRAINT_ATOM_COUNTS[kind]
    fields = [part.strip() for part in body.split(",")]
    if len(fields) != expected_atoms + 1:
        raise XtbConstraintSyntaxError(
            f"xTB $constrain directive {line.strip()!r} has {len(fields)} fields; "
            f"'{kind}' requires exactly {expected_atoms} atom indices followed by one "
            f"numeric target ({expected_atoms + 1} fields). xTB only warns "
            "(\"not enough arguments to constrain a distance\") and optimizes WITHOUT "
            "the constraint. Set the strength on a separate 'force constant=0.5' line "
            "instead of an extra per-line token.",
            line=line,
        )
    atoms: list[int] = []
    for raw in fields[:expected_atoms]:
        try:
            atom = int(raw)
        except ValueError as exc:
            raise XtbConstraintSyntaxError(
                f"xTB $constrain directive {line.strip()!r}: atom index {raw!r} is not "
                "an integer (xTB uses 1-based atom indices).",
                line=line,
            ) from exc
        if atom < 1:
            raise XtbConstraintSyntaxError(
                f"xTB $constrain directive {line.strip()!r}: atom index {atom} is not "
                "1-based (xTB atom indices start at 1).",
                line=line,
            )
        atoms.append(atom)
    raw_value = fields[expected_atoms]
    try:
        target = float(raw_value)
    except ValueError as exc:
        raise XtbConstraintSyntaxError(
            f"xTB $constrain directive {line.strip()!r}: target value {raw_value!r} is "
            "not numeric. xTB would ignore this constraint and still exit 0.",
            line=line,
        ) from exc
    if not math.isfinite(target):
        raise XtbConstraintSyntaxError(
            f"xTB $constrain directive {line.strip()!r}: target value {raw_value!r} "
            "is not finite.",
            line=line,
        )
    return {"kind": kind, "atoms": tuple(atoms), "target": target, "line": line.strip()}


def parse_xcontrol_constraints(text: str) -> dict[str, Any]:
    """Parse every ``$constrain`` block in *text*.

    Args:
        text: Raw xcontrol content (may contain ``$fix``/``$opt`` blocks; they
            are ignored — only ``$constrain`` is validated).

    Returns:
        ``{"directives": [...], "force_constant": float | None, "blocks": N}``.

    Raises:
        XtbConstraintSyntaxError: A directive xTB would silently ignore.
    """
    directives: list[dict[str, Any]] = []
    force_constant: float | None = None
    blocks = 0
    in_block = False
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("$constrain"):
            if in_block:
                raise XtbConstraintSyntaxError(
                    "nested $constrain block: the previous block is never closed",
                    line=stripped,
                )
            in_block = True
            blocks += 1
            continue
        if stripped.startswith("$end"):
            in_block = False
            continue
        if stripped.startswith("$"):
            # Other xcontrol blocks ($fix/$opt/...) are not ours to validate.
            in_block = False
            continue
        if not in_block:
            continue
        force_match = _FORCE_CONSTANT_LINE_RE.match(raw_line)
        if force_match is not None:
            force_constant = float(force_match.group("value"))
            continue
        line_match = _CONSTRAINT_LINE_RE.match(raw_line)
        if line_match is None:
            raise XtbConstraintSyntaxError(
                f"unrecognised $constrain directive {stripped!r}; expected "
                "'distance: i, j, value', 'angle: i, j, k, value', "
                "'dihedral: i, j, k, l, value' or 'force constant=<value>'.",
                line=stripped,
            )
        kind = line_match.group("kind").lower()
        if kind not in _CONSTRAINT_ATOM_COUNTS:
            raise XtbConstraintSyntaxError(
                f"unknown $constrain directive kind {kind!r} in {stripped!r}; xTB "
                "accepts distance/angle/dihedral only.",
                line=stripped,
            )
        directives.append(_parse_constraint_line(kind, line_match.group("body"), raw_line))
    if in_block:
        raise XtbConstraintSyntaxError(
            "unterminated $constrain block: missing $end", line="$constrain"
        )
    return {
        "directives": directives,
        "force_constant": force_constant,
        "blocks": blocks,
    }


def validate_xcontrol_text(text: str) -> dict[str, Any]:
    """Validate raw xcontrol text; raise the typed refusal on any bad directive.

    This is the submit-time seam for user-supplied ``.xcontrol``/``.inp`` text
    (raw path payloads): xTB tolerates malformed directives at runtime, so the
    platform must reject them BEFORE execution instead of reporting a
    completed but unconstrained optimization.
    """
    return parse_xcontrol_constraints(text)


def detect_constraint_rejection(log_text: str, *, limit: int = 5) -> tuple[str, ...]:
    """Return xTB lines showing a requested constraint was refused/ignored."""
    lines: list[str] = []
    for raw_line in str(log_text or "").splitlines():
        if _CONSTRAINT_REJECTION_RE.search(raw_line):
            lines.append(raw_line.strip())
            if len(lines) >= limit:
                break
    return tuple(lines)


def parse_constraint_evidence(log_text: str, *, requested: int) -> dict[str, Any]:
    """Evidence record: did xTB refuse a requested constraint?

    A run whose constraint was dropped must FAIL (never COMPLETED): xTB
    prints a warning and keeps optimizing unconstrained, exiting 0.
    """
    rejection_lines = detect_constraint_rejection(log_text)
    rejected = bool(rejection_lines)
    return {
        "constraints_requested": int(requested),
        "rejected": rejected,
        "rule": "constraints_not_honored" if rejected else "",
        "field": "constraints" if rejected else "",
        "rejection_lines": list(rejection_lines),
    }


def xcontrol_constraint_block(constraints: Sequence[CoordinateConstraint]) -> str:
    """Render xTB xcontrol ``$constrain`` lines for *constraints*.

    xTB uses 1-based atom indices and the per-line syntax
    ``<kind>: <i>, <j>[, <k>[, <l>]], <value>[, <force_constant>]``.

    Force-constant capability note (verified on xTB 6.7.1): the per-line
    fourth argument is silently IGNORED by that binary, while the block-level
    ``force constant=`` line is honored.  When every constraint carries the
    same explicit force constant we therefore emit the block-level line
    instead of the per-line argument; mixed or unset force constants keep the
    per-line form (xTB then uses its automatic strength).

    This builder only ever emits the syntax xTB honors — ``kind: i, j, value``
    (1-based) plus an optional ``force constant=<value>`` block line.  It
    validates atom counts / numeric targets first and raises
    :class:`XtbConstraintSyntaxError` instead of rendering a directive that
    xTB would ignore with a warning while still exiting 0.
    """
    if not constraints:
        return ""
    for constraint in constraints:
        kind = _constraint_kind(constraint)
        expected = _CONSTRAINT_ATOM_COUNTS[kind]
        atoms = tuple(constraint.atoms)
        if len(atoms) != expected:
            raise XtbConstraintSyntaxError(
                f"{kind} constraint requires {expected} atom indices, got "
                f"{len(atoms)}: {atoms!r}",
            )
        if any(not isinstance(atom, int) or atom < 0 for atom in atoms):
            raise XtbConstraintSyntaxError(
                f"{kind} constraint atom indices must be non-negative integers: {atoms!r}",
            )
        if not math.isfinite(float(constraint.target)):
            raise XtbConstraintSyntaxError(
                f"{kind} constraint target must be finite, got {constraint.target!r}",
            )
        if constraint.force_constant is not None and not math.isfinite(
            float(constraint.force_constant)
        ):
            raise XtbConstraintSyntaxError(
                f"{kind} constraint force constant must be finite, got "
                f"{constraint.force_constant!r}",
            )
    lines = ["$constrain"]
    force_constants = {
        float(c.force_constant) for c in constraints if c.force_constant is not None
    }
    uniform_force_constant = (
        len(force_constants) == 1
        and all(c.force_constant is not None for c in constraints)
    )
    if uniform_force_constant:
        lines.append(f"  force constant={next(iter(force_constants)):.4f}")
    for c in constraints:
        if isinstance(c, DistanceConstraint):
            kind = "distance"
        elif isinstance(c, AngleConstraint):
            kind = "angle"
        else:
            kind = "dihedral"
        atoms = ", ".join(str(a + 1) for a in c.atoms)
        line = f"  {kind}: {atoms}, {c.target:.6f}"
        if c.force_constant is not None and not uniform_force_constant:
            line += f", {c.force_constant:.4f}"
        lines.append(line)
    lines.append("$end")
    return "\n".join(lines)


def plan_from_dict(data: dict[str, Any]) -> ReactionCoordinatePlan:
    """Build a :class:`ReactionCoordinatePlan` from a plain JSON-style dict."""
    coordinates_raw = data.get("coordinates")
    if not isinstance(coordinates_raw, list):
        raise ValueError("scan_plan: 'coordinates' must be a list")
    coordinates = tuple(CoordinateSpec.from_dict(c) for c in coordinates_raw)
    return ReactionCoordinatePlan(
        coordinates=coordinates,
        points=int(data.get("points") or 21),
        coupling=str(data.get("coupling") or "synchronous"),  # type: ignore[arg-type]
        start_from=str(data.get("start_from") or "reactant"),  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class RelaxedScanPoint:
    """One optimized frame of a relaxed scan.

    Attributes:
        frame_index: Index in [0, points).
        progress: Synchronous progress λ = frame_index/(points−1).
        coordinates: Optimized geometry (Å, N×3) or None on failure.
        symbols: Element symbols or None on failure.
        energy_hartree: Converged total energy or None on failure.
        success: Whether the frame optimization converged.
        coordinate_values: Coordinate id → target value at this frame.
    """

    frame_index: int
    progress: float
    coordinates: NDArray[np.float64] | None
    symbols: list[str] | None
    energy_hartree: float | None
    success: bool
    coordinate_values: dict[str, float] = field(default_factory=dict)
    # Free-form provenance: retry history / SCF state for the frame
    # (populated by the pointwise ORCA scan driver; empty on native scans).
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RelaxedScanResult:
    """Aggregated relaxed-scan outcome.

    Attributes:
        points: Per-frame results (one per plan frame).
        input_xyz: Input geometry used for the first frame.
        scan_dir: Directory holding all frame outputs.
        success: Whether the scan completed (or aborted early on failure).
        message: Human-readable status / failure note.
    """

    points: list[RelaxedScanPoint]
    input_xyz: Path
    scan_dir: Path
    success: bool
    message: str = ""

    def energies(self) -> list[Optional[float]]:
        return [p.energy_hartree for p in self.points]

    def best_point(self) -> Optional[RelaxedScanPoint]:
        """Frame with the lowest converged energy (or None when empty)."""
        converged = [p for p in self.points if p.success and p.energy_hartree is not None]
        if not converged:
            return None
        return min(converged, key=lambda p: float(p.energy_hartree))

    def __len__(self) -> int:
        return len(self.points)


def _constraint_kind(constraint: CoordinateConstraint) -> str:
    """xTB directive kind for one typed constraint object."""
    if isinstance(constraint, DistanceConstraint):
        return "distance"
    if isinstance(constraint, AngleConstraint):
        return "angle"
    return "dihedral"


__all__ = [
    "RelaxedScanPoint",
    "RelaxedScanResult",
    "XtbConstraintSyntaxError",
    "detect_constraint_rejection",
    "parse_constraint_evidence",
    "parse_xcontrol_constraints",
    "plan_from_dict",
    "validate_xcontrol_text",
    "xcontrol_constraint_block",
]
