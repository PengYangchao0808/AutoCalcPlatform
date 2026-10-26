"""Typed scientific inputs of a research plan (M1-04a / D06 + G10).

A research plan is not a job payload: charge, multiplicity, the electronic
state, the provenance of every structure and the geometric constraints are
*scientific values*.  Filling a missing charge with ``0`` or a missing
multiplicity with ``1`` silently changes the science (an anion becomes a
neutral molecule, a triplet becomes a singlet), so those values are never
defaulted here.  A caller that omits a critical value receives a typed
**clarification** instead of a fabricated default.

Vocabulary (the single implementation for the platform side):

``charge``
    Signed integer, any magnitude.  ``-1`` stays ``-1``; it is never folded,
    abs()-ed or "normalized" to zero.
``multiplicity``
    Integer ``>= 1``.  ``2``/``3`` (open shell) are preserved as given.
``electronic_state``
    Optional typed block ``{state_label, reference_state, spin_polarization}``.
    When ``spin_polarization`` is present it must equal ``multiplicity - 1``;
    when an ``electron_count`` is supplied the electron parity must match the
    multiplicity parity (even electrons <-> odd multiplicity).
``structure_provenance``
    The digest-bound identity of the input structure bytes
    (``{input_id, sha256, format, role, source}``).  A structure without
    provenance cannot be re-derived, so it is a clarification, never a guess.
``constraints``
    Typed geometric constraints (bond/angle/dihedral/distance/coordinate) with
    0-based atom indices and a numeric target.  They are forwarded verbatim and
    never re-indexed; the science owner (CCCP) interprets them.

This module is deliberately small and stdlib-only: ``acp_services`` must stay
importable without ``acp`` and without ``cccp`` (the science descriptors are
A's; parsing a structure is A's too).

Author: ACP Services (B lane, M1-04a).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CONSTRAINT_KINDS",
    "CRITICAL_SCIENCE_FIELDS",
    "SCIENCE_INPUT_FIELDS",
    "SCIENCE_INPUT_SCHEMA",
    "ScienceClarification",
    "ScienceConstraint",
    "ScienceInputError",
    "ScienceInputIssue",
    "ScienceInputs",
    "ScienceInputVerdict",
    "ElectronicState",
    "StructureProvenance",
    "normalize_science_inputs",
    "require_complete_science_inputs",
]

#: Contract identity of this vocabulary (registered by the D06 digest-domain
#: table as the canonical input order of ``plan_definition_digest``).
SCIENCE_INPUT_SCHEMA = "acp2.runtime.science-input.v1"

#: Canonical field order.  A digest projection that includes these fields MUST
#: use this order (D06: "canonical 输入与字段序").
SCIENCE_INPUT_FIELDS: tuple[str, ...] = (
    "method",
    "mapping",
    "charge",
    "multiplicity",
    "electronic_state",
    "structure_provenance",
    "constraints",
)

#: The two values that are never defaulted (0/1 are scientific decisions).
CRITICAL_SCIENCE_FIELDS: tuple[str, ...] = ("charge", "multiplicity")

#: Allowed constraint kinds (CCCP owns the numeric semantics).
CONSTRAINT_KINDS: tuple[str, ...] = (
    "bond",
    "angle",
    "dihedral",
    "distance",
    "coordinate",
    "atom_index",
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_CONSTRAINTS = 256


class ScienceInputError(ValueError):
    """A science-input payload violates the frozen vocabulary (typed)."""

    def __init__(self, rule: str, message: str, *, params: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message
        self.params = dict(params or {})

    def detail(self) -> dict[str, Any]:
        return {"code": "input_invalid", "message": self.message, "params": {"rule": self.rule}}


@dataclass(frozen=True, slots=True)
class ScienceClarification:
    """One missing critical scientific value (a question, never a default)."""

    field_name: str
    reason: str = "clarification_required"

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field_name, "rule": self.reason}


@dataclass(frozen=True, slots=True)
class ScienceInputIssue:
    """One rejected scientific value (a validation failure, not a question)."""

    field_name: str
    rule: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field_name, "rule": self.rule, "message": self.message}


@dataclass(frozen=True, slots=True)
class ElectronicState:
    """Typed electronic state (charge/multiplicity stay the authority)."""

    charge: int
    multiplicity: int
    state_label: str = ""
    reference_state: str = ""
    spin_polarization: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.charge, bool) or not isinstance(self.charge, int):
            raise ScienceInputError("invalid_charge", "electronic_state.charge must be an integer")
        if isinstance(self.multiplicity, bool) or not isinstance(self.multiplicity, int):
            raise ScienceInputError(
                "invalid_multiplicity", "electronic_state.multiplicity must be an integer"
            )
        if self.multiplicity < 1:
            raise ScienceInputError("invalid_multiplicity", "multiplicity must be >= 1")
        if self.spin_polarization is not None:
            if isinstance(self.spin_polarization, bool) or not isinstance(
                self.spin_polarization, int
            ):
                raise ScienceInputError(
                    "invalid_spin_polarization", "spin_polarization must be an integer 2S"
                )
            if self.spin_polarization != self.multiplicity - 1:
                raise ScienceInputError(
                    "spin_multiplicity_mismatch",
                    f"2S={self.spin_polarization} requires multiplicity "
                    f"{self.spin_polarization + 1}, got {self.multiplicity}",
                )

    @property
    def open_shell(self) -> bool:
        """Whether the state is open shell (multiplicity > 1)."""
        return self.multiplicity > 1

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }
        if self.state_label:
            payload["state_label"] = self.state_label
        if self.reference_state:
            payload["reference_state"] = self.reference_state
        if self.spin_polarization is not None:
            payload["spin_polarization"] = self.spin_polarization
        return payload


@dataclass(frozen=True, slots=True)
class StructureProvenance:
    """Digest-bound provenance of one input structure (never a bare path)."""

    input_id: str
    sha256: str
    format: str
    role: str = "minimum"
    source: str = ""

    def __post_init__(self) -> None:
        if not str(self.input_id or "").strip():
            raise ScienceInputError(
                "invalid_structure_provenance", "structure_provenance.input_id is required"
            )
        if _DIGEST_RE.match(str(self.sha256 or "")) is None:
            raise ScienceInputError(
                "invalid_structure_provenance",
                "structure_provenance.sha256 must be sha256:<64 lowercase hex>",
            )
        if not str(self.format or "").strip():
            raise ScienceInputError(
                "invalid_structure_provenance", "structure_provenance.format is required"
            )
        if _ROLE_RE.match(str(self.role or "")) is None:
            raise ScienceInputError(
                "invalid_structure_provenance",
                f"structure_provenance.role {self.role!r} must match {_ROLE_RE.pattern}",
            )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "input_id": self.input_id,
            "sha256": self.sha256,
            "format": self.format,
            "role": self.role,
        }
        if self.source:
            payload["source"] = self.source
        return payload


@dataclass(frozen=True, slots=True)
class ScienceConstraint:
    """One typed geometric constraint (0-based atom indices)."""

    kind: str
    atoms: tuple[int, ...]
    target: str
    unit: str = ""

    def __post_init__(self) -> None:
        if str(self.kind) not in CONSTRAINT_KINDS:
            raise ScienceInputError(
                "invalid_constraint",
                f"constraint kind {self.kind!r} is not one of {list(CONSTRAINT_KINDS)}",
            )
        expected = {
            "bond": 2,
            "distance": 2,
            "angle": 3,
            "dihedral": 4,
            "coordinate": 3,
            "atom_index": 1,
        }[str(self.kind)]
        if len(self.atoms) != expected:
            raise ScienceInputError(
                "invalid_constraint",
                f"constraint {self.kind} needs {expected} atom indices, got {len(self.atoms)}",
            )
        for index in self.atoms:
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ScienceInputError(
                    "invalid_constraint", "constraint atom indices must be integers >= 0"
                )
        if not str(self.target or "").strip():
            raise ScienceInputError("invalid_constraint", "constraint target is required")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "atoms": list(self.atoms),
            "target": self.target,
        }
        if self.unit:
            payload["unit"] = self.unit
        return payload


@dataclass(frozen=True, slots=True)
class ScienceInputs:
    """The normalized, digest-ready typed science block."""

    charge: int
    multiplicity: int
    method: str = ""
    mapping: str = ""
    electronic_state: ElectronicState | None = None
    structure_provenance: tuple[StructureProvenance, ...] = ()
    constraints: tuple[ScienceConstraint, ...] = ()
    electron_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical projection in :data:`SCIENCE_INPUT_FIELDS` order."""
        payload: dict[str, Any] = {}
        if self.method:
            payload["method"] = self.method
        if self.mapping:
            payload["mapping"] = self.mapping
        payload["charge"] = self.charge
        payload["multiplicity"] = self.multiplicity
        if self.electronic_state is not None:
            payload["electronic_state"] = self.electronic_state.to_dict()
        if self.structure_provenance:
            payload["structure_provenance"] = [item.to_dict() for item in self.structure_provenance]
        if self.constraints:
            payload["constraints"] = [item.to_dict() for item in self.constraints]
        return payload


@dataclass(frozen=True, slots=True)
class ScienceInputVerdict:
    """Outcome of one science-input validation (complete, clarify or reject)."""

    complete: bool
    clarifications: tuple[ScienceClarification, ...] = ()
    issues: tuple[ScienceInputIssue, ...] = ()
    values: ScienceInputs | None = None
    electron_parity_checked: bool = False

    @property
    def clarification_required(self) -> bool:
        return bool(self.clarifications)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCIENCE_INPUT_SCHEMA,
            "complete": self.complete,
            "clarification_required": self.clarification_required,
            "clarifications": [item.to_dict() for item in self.clarifications],
            "issues": [item.to_dict() for item in self.issues],
            "electron_parity_checked": self.electron_parity_checked,
            "values": None if self.values is None else self.values.to_dict(),
        }


def _optional_int(payload: Mapping[str, Any], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScienceInputError(
            "invalid_integer", f"{name} must be an explicit integer", params={"field": name}
        )
    return value


def _structure_provenance(value: Any) -> tuple[StructureProvenance, ...]:
    sequence = isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    items = value if sequence else [value]
    provenance: list[StructureProvenance] = []
    for item in items:
        if isinstance(item, StructureProvenance):
            provenance.append(item)
            continue
        if not isinstance(item, Mapping):
            raise ScienceInputError(
                "invalid_structure_provenance",
                "structure_provenance entries must be objects",
                params={"field": "structure_provenance"},
            )
        provenance.append(
            StructureProvenance(
                input_id=str(item.get("input_id") or ""),
                sha256=str(item.get("sha256") or ""),
                format=str(item.get("format") or ""),
                role=str(item.get("role") or "minimum"),
                source=str(item.get("source") or ""),
            )
        )
    return tuple(provenance)


def _constraints(value: Any) -> tuple[ScienceConstraint, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ScienceInputError(
            "invalid_constraint", "constraints must be an array", params={"field": "constraints"}
        )
    if len(value) > _MAX_CONSTRAINTS:
        raise ScienceInputError(
            "invalid_constraint",
            f"at most {_MAX_CONSTRAINTS} constraints are accepted",
            params={"field": "constraints"},
        )
    parsed: list[ScienceConstraint] = []
    for item in value:
        if isinstance(item, ScienceConstraint):
            parsed.append(item)
            continue
        if not isinstance(item, Mapping):
            raise ScienceInputError(
                "invalid_constraint",
                "each constraint must be an object",
                params={"field": "constraints"},
            )
        atoms_raw = item.get("atoms")
        if not isinstance(atoms_raw, Sequence) or isinstance(atoms_raw, (str, bytes)):
            raise ScienceInputError(
                "invalid_constraint",
                "constraint.atoms must be an array of integer indices",
                params={"field": "constraints"},
            )
        target = item.get("target")
        if isinstance(target, bool) or not isinstance(target, (int, str)):
            raise ScienceInputError(
                "invalid_constraint",
                "constraint.target must be an integer or a decimal string",
                params={"field": "constraints"},
            )
        parsed.append(
            ScienceConstraint(
                kind=str(item.get("kind") or ""),
                atoms=tuple(int(index) for index in atoms_raw),
                target=str(target),
                unit=str(item.get("unit") or ""),
            )
        )
    return tuple(parsed)


def normalize_science_inputs(payload: Mapping[str, Any] | None) -> ScienceInputVerdict:
    """Validate one typed science block; never fabricate ``0``/``1``.

    Args:
        payload: The candidate science block.  ``charge`` and ``multiplicity``
            must be present; an absent or ``null`` value produces a
            :class:`ScienceClarification`, not a default.

    Returns:
        A :class:`ScienceInputVerdict`.  ``complete`` is true only when the two
        critical values are explicitly present and every present value is
        valid; a rejected value is an issue (never silently dropped).

    Raises:
        ScienceInputError: The payload itself is not an object, or a value is
            structurally invalid (unknown field, wrong type).
    """
    source: Mapping[str, Any] = payload or {}
    if not isinstance(source, Mapping):
        raise ScienceInputError("root_not_object", "science input must be an object")
    known = set(SCIENCE_INPUT_FIELDS) | {"structure", "electron_count", "workflow"}
    unknown = sorted(str(key) for key in source if key not in known)
    if unknown:
        raise ScienceInputError(
            "unknown_science_field",
            "science input carries unknown field(s): " + ", ".join(unknown),
            params={"fields": unknown},
        )

    clarifications: list[ScienceClarification] = []
    issues: list[ScienceInputIssue] = []

    charge = multiplicity = None
    for name in CRITICAL_SCIENCE_FIELDS:
        try:
            if name == "charge":
                charge = _optional_int(source, name)
            else:
                multiplicity = _optional_int(source, name)
        except ScienceInputError as exc:
            issues.append(ScienceInputIssue(field_name=name, rule=exc.rule, message=exc.message))
            continue
        value = source.get(name)
        if value is None:
            clarifications.append(ScienceClarification(name))

    method = str(source.get("method") or "")
    mapping = str(source.get("mapping") or "")

    electronic_state: ElectronicState | None = None
    raw_state = source.get("electronic_state")
    if raw_state is not None and charge is not None and multiplicity is not None:
        if not isinstance(raw_state, Mapping):
            issues.append(
                ScienceInputIssue(
                    field_name="electronic_state",
                    rule="invalid_electronic_state",
                    message="electronic_state must be an object",
                )
            )
        else:
            try:
                electronic_state = ElectronicState(
                    charge=charge,
                    multiplicity=multiplicity,
                    state_label=str(raw_state.get("state_label") or ""),
                    reference_state=str(raw_state.get("reference_state") or ""),
                    spin_polarization=_optional_int(raw_state, "spin_polarization"),
                )
            except ScienceInputError as exc:
                issues.append(
                    ScienceInputIssue(
                        field_name="electronic_state", rule=exc.rule, message=exc.message
                    )
                )

    provenance: tuple[StructureProvenance, ...] = ()
    raw_provenance = source.get("structure_provenance")
    if raw_provenance is not None:
        try:
            provenance = _structure_provenance(raw_provenance)
        except ScienceInputError as exc:
            issues.append(
                ScienceInputIssue(
                    field_name="structure_provenance", rule=exc.rule, message=exc.message
                )
            )
    elif source.get("structure") is not None:
        clarifications.append(ScienceClarification("structure_provenance"))

    constraints: tuple[ScienceConstraint, ...] = ()
    if source.get("constraints") is not None:
        try:
            constraints = _constraints(source.get("constraints"))
        except ScienceInputError as exc:
            issues.append(
                ScienceInputIssue(field_name="constraints", rule=exc.rule, message=exc.message)
            )

    electron_count: int | None = None
    try:
        electron_count = _optional_int(source, "electron_count")
    except ScienceInputError as exc:
        issues.append(
            ScienceInputIssue(field_name="electron_count", rule=exc.rule, message=exc.message)
        )

    parity_checked = False
    if (
        electron_count is not None
        and charge is not None
        and multiplicity is not None
        and not issues
    ):
        electrons = electron_count - charge
        if electrons < 1:
            issues.append(
                ScienceInputIssue(
                    field_name="charge",
                    rule="invalid_electron_count",
                    message=(
                        f"electron_count {electron_count} - charge {charge} leaves no electrons"
                    ),
                )
            )
        else:
            parity_checked = True
            if electrons % 2 != (multiplicity - 1) % 2:
                issues.append(
                    ScienceInputIssue(
                        field_name="multiplicity",
                        rule="spin_multiplicity_mismatch",
                        message=(
                            f"{electrons} electrons (electron_count {electron_count}, "
                            f"charge {charge}) require multiplicity "
                            f"{'odd' if electrons % 2 == 0 else 'even'}; got {multiplicity}"
                        ),
                    )
                )

    values: ScienceInputs | None = None
    if not clarifications and not issues and charge is not None and multiplicity is not None:
        values = ScienceInputs(
            charge=charge,
            multiplicity=multiplicity,
            method=method,
            mapping=mapping,
            electronic_state=electronic_state,
            structure_provenance=provenance,
            constraints=constraints,
            electron_count=electron_count,
        )

    return ScienceInputVerdict(
        complete=values is not None,
        clarifications=tuple(clarifications),
        issues=tuple(issues),
        values=values,
        electron_parity_checked=parity_checked,
    )


def require_complete_science_inputs(payload: Mapping[str, Any] | None) -> ScienceInputs:
    """Return the typed science block or raise the typed clarification error.

    Raises:
        ScienceInputError: rule ``clarification_required`` when a critical
            value is missing, or the first issue rule when a value is invalid.
            The message names the field so a client can ask a human instead of
            guessing a scientific default.
    """
    verdict = normalize_science_inputs(payload)
    if verdict.values is not None:
        return verdict.values
    if verdict.clarifications:
        missing = ", ".join(item.field_name for item in verdict.clarifications)
        raise ScienceInputError(
            "clarification_required",
            f"science input is missing: {missing}",
            params={"fields": [item.field_name for item in verdict.clarifications]},
        )
    first = verdict.issues[0]
    raise ScienceInputError(first.rule, first.message, params={"field": first.field_name})
