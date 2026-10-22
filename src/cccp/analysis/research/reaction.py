"""Public ACP2 reaction science type (``reaction.schema.json``, major v1).

The frozen JSON contract in
``docs/reference/next-change/acp2-contracts/science/reaction.schema.json``
describes a net reaction or a declared elementary step with stable atom
identity on both sides.  This module provides the immutable Python mirror:
frozen dataclasses with explicit ``to_dict``/``from_dict``, a strict
schema-version check and a historical read adapter.

Two boundaries are deliberate:

* **Pure types, no execution.** Importing this module must never import
  ``cccp.calculation.tasks``, ``cccp.qc`` or ``acp``.  The only non-stdlib
  import is a lazy adapter that materialises the existing
  :class:`cccp.calculation.contracts.StructureArtifact` when a caller already
  has a concrete file path.
* **Unknowns stay unknown.** A missing condition or stereochemical assignment
  is preserved (and must be declared in ``input_uncertainty``); it is never
  replaced by a default value.

This is a serialized contract type, not a second structure model.  The
authoritative structure and electronic-state models remain
``cccp.calculation.contracts.StructureArtifact`` /
``cccp.analysis.structure.models.StructureAsset`` and
``cccp.calculation.contracts.ElectronicStateSpec``.

Author: QCcalc Team
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from cccp.analysis.research._common import (
    ForbiddenPlatformFieldError,
    ScienceContractError,
    canonical_json,
    is_formula,
    read_schema_version,
    reject_forbidden_keys,
    reject_unknown_keys,
    require_int,
    require_keys,
    require_mapping,
    require_number,
    require_sequence,
    require_string,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from cccp.calculation.contracts import StructureArtifact

logger = logging.getLogger(__name__)

__all__ = [
    "ATOM_ID_SCHEMES",
    "BOND_CHANGE_TYPES",
    "INPUT_FORMATS",
    "PHASES",
    "REACTION_SCOPES",
    "SCHEMA_VERSION",
    "STANDARD_STATES",
    "STEREO_BOND_CONFIGURATIONS",
    "STEREO_CENTER_CONFIGURATIONS",
    "STEREO_STATUSES",
    "AtomIdentityError",
    "AtomMapping",
    "BondChange",
    "BondChangeError",
    "Conditions",
    "DuplicateMapError",
    "HistoricalReactionRecord",
    "InputUncertainty",
    "InventedStereoError",
    "MapCoverageError",
    "MapInverseError",
    "MissingProductError",
    "MissingReactantError",
    "Reaction",
    "ReactionForbiddenFieldError",
    "ReactionSchemaError",
    "ReactionTypeError",
    "StereoCenter",
    "StereoContradictionError",
    "StereoDoubleBond",
    "Stereochemistry",
    "StructureRef",
    "UnknownDeclarationError",
    "UnsupportedSchemaVersionError",
    "canonical_json",
    "read_historical_reaction",
]

SCHEMA_VERSION: Final = 1
REACTION_SCOPES: Final = frozenset({"net_reaction", "elementary_step"})
INPUT_FORMATS: Final = frozenset({"mol", "sdf", "smiles", "structure_upload"})
ATOM_ID_SCHEMES: Final = frozenset({"explicit", "input_order_0_based"})
STEREO_STATUSES: Final = frozenset({"defined", "partially_defined", "unknown"})
STEREO_CENTER_CONFIGURATIONS: Final = frozenset({"R", "S", "unknown"})
STEREO_BOND_CONFIGURATIONS: Final = frozenset({"E", "Z", "unknown"})
BOND_CHANGE_TYPES: Final = frozenset({"formed", "broken", "changed"})
STANDARD_STATES: Final = frozenset({"1_bar_ideal_gas", "1_molar_ideal_solution", "unknown"})
PHASES: Final = frozenset({"gas", "solution", "liquid", "solid", "unknown"})

_REACTION_KEYS: Final = frozenset(
    {
        "schema_version",
        "reaction_id",
        "reaction_scope",
        "left",
        "right",
        "atom_mapping",
        "bond_changes",
        "conditions",
        "input_uncertainty",
    }
)
_STRUCTURE_KEYS: Final = frozenset(
    {
        "structure_id",
        "species",
        "formula",
        "input_format",
        "atom_id_scheme",
        "atom_ids",
        "elements",
        "charge",
        "multiplicity",
        "stereochemistry",
    }
)
_STEREOCHEMISTRY_KEYS: Final = frozenset({"status", "centers", "double_bonds"})
_STEREO_CENTER_KEYS: Final = frozenset({"atom_id", "configuration"})
_STEREO_BOND_KEYS: Final = frozenset({"atom_ids", "configuration"})
_MAPPING_KEYS: Final = frozenset(
    {"left_to_right", "right_to_left", "unmapped_left", "unmapped_right"}
)
_BOND_CHANGE_KEYS: Final = frozenset(
    {"change_type", "left_atoms", "right_atoms", "bond_order_left", "bond_order_right"}
)
_CONDITION_KEYS: Final = frozenset(
    {"temperature_k", "pressure_pa", "standard_state", "solvent", "phase"}
)
_UNCERTAINTY_KEYS: Final = frozenset({"unknown_fields", "notes"})


# ── typed errors ────────────────────────────────────────────────────────────


class ReactionTypeError(ScienceContractError):
    """Base class for a rejected reaction payload."""


class UnsupportedSchemaVersionError(ReactionTypeError):
    """The payload declares a reaction contract major version other than 1."""


class ReactionSchemaError(ReactionTypeError):
    """A reaction payload does not have the frozen v1 structure."""


class ReactionForbiddenFieldError(ReactionTypeError):
    """A reaction payload carries a forbidden platform/model field."""


class MissingReactantError(ReactionTypeError):
    """The left side is absent or empty."""


class MissingProductError(ReactionTypeError):
    """The right side is absent or empty — an incomplete transformation."""


class AtomIdentityError(ReactionTypeError):
    """An atom id is duplicated or a canonical atom address is malformed."""


class DuplicateMapError(ReactionTypeError):
    """One atom is claimed more than once by the same mapping direction."""


class MapInverseError(ReactionTypeError):
    """``left_to_right`` and ``right_to_left`` are not exact inverses."""


class MapCoverageError(ReactionTypeError):
    """An atom is mapped and unmapped, or omitted entirely."""


class BondChangeError(ReactionTypeError):
    """A bond change is malformed for its declared shape."""


class UnknownDeclarationError(ReactionTypeError):
    """A null condition is missing from ``input_uncertainty.unknown_fields``."""


class InventedStereoError(ReactionTypeError):
    """A stereochemical assignment contradicts a declared unknown fact."""


class StereoContradictionError(ReactionTypeError):
    """A stereochemistry status contradicts its own centers/double bonds."""


# ── helpers ─────────────────────────────────────────────────────────────────


def _string_tuple(value: Any, where: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    items = require_sequence(value, where)
    if not items and not allow_empty:
        raise ReactionSchemaError(f"{where}: must not be empty")
    result = tuple(require_string(item, f"{where}[{index}]") for index, item in enumerate(items))
    if len(set(result)) != len(result):
        raise AtomIdentityError(f"{where}: duplicate entries {sorted(result)}")
    return result


def _address_parts(address: Any, where: str) -> tuple[str, str]:
    text = require_string(address, where)
    structure_id, separator, atom_id = text.partition(":")
    if not separator or not structure_id or not atom_id or text.count(":") != 1:
        raise AtomIdentityError(
            f"{where}: malformed atom address {text!r}; expected '<structure_id>:<atom_id>'"
        )
    for part in (structure_id, atom_id):
        if not all(char.isalnum() or char in "_.-" for char in part):
            raise AtomIdentityError(f"{where}: unsupported characters in atom address {text!r}")
    return structure_id, atom_id


def _address_set(addresses: Any, where: str) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for index, raw in enumerate(require_sequence(addresses, where)):
        text = require_string(raw, f"{where}[{index}]")
        result[text] = _address_parts(text, f"{where}[{index}]")
    return result


def _coerce_stereochemistry(
    value: Stereochemistry | Mapping[str, Any], where: str
) -> Stereochemistry:
    if isinstance(value, Stereochemistry):
        return value
    return Stereochemistry.from_dict(require_mapping(value, where), where)


def _coerce_centers(value: Any, where: str) -> tuple[StereoCenter, ...]:
    items = require_sequence(value, where)
    centers = tuple(
        item
        if isinstance(item, StereoCenter)
        else StereoCenter.from_dict(require_mapping(item, f"{where}[{index}]"), f"{where}[{index}]")
        for index, item in enumerate(items)
    )
    return centers


def _coerce_double_bonds(value: Any, where: str) -> tuple[StereoDoubleBond, ...]:
    items = require_sequence(value, where)
    bonds = tuple(
        item
        if isinstance(item, StereoDoubleBond)
        else StereoDoubleBond.from_dict(
            require_mapping(item, f"{where}[{index}]"), f"{where}[{index}]"
        )
        for index, item in enumerate(items)
    )
    return bonds


def _optional_order(value: Any, where: str) -> int | float | None:
    if value is None:
        return None
    number = require_number(value, where)
    if number < 0:
        raise BondChangeError(f"{where}: bond order must be >= 0, got {number!r}")
    return number


# ── stereochemistry ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StereoCenter:
    """One declared tetrahedral stereocenter.

    ``configuration`` may be ``"unknown"``; an unknown input fact is preserved
    verbatim and is never promoted to an inferred ``R``/``S`` assignment.
    """

    atom_id: str
    configuration: str

    def __post_init__(self) -> None:
        require_string(self.atom_id, "stereo_center.atom_id")
        if self.configuration not in STEREO_CENTER_CONFIGURATIONS:
            raise ReactionSchemaError(
                "stereo_center.configuration must be one of "
                f"{sorted(STEREO_CENTER_CONFIGURATIONS)}, got {self.configuration!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping."""
        return {"atom_id": self.atom_id, "configuration": self.configuration}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "stereo_center") -> StereoCenter:
        """Build one stereocenter from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _STEREO_CENTER_KEYS, where)
        require_keys(data, _STEREO_CENTER_KEYS, where)
        return cls(
            atom_id=require_string(data["atom_id"], f"{where}.atom_id"),
            configuration=require_string(data["configuration"], f"{where}.configuration"),
        )


@dataclass(frozen=True, slots=True)
class StereoDoubleBond:
    """One declared double-bond configuration (``E``/``Z`` or ``unknown``)."""

    atom_ids: tuple[str, str]
    configuration: str

    def __post_init__(self) -> None:
        ids = tuple(self.atom_ids)
        if len(ids) != 2:
            raise ReactionSchemaError("stereo_double_bond.atom_ids must contain exactly two ids")
        for index, atom_id in enumerate(ids):
            require_string(atom_id, f"stereo_double_bond.atom_ids[{index}]")
        object.__setattr__(self, "atom_ids", (ids[0], ids[1]))
        if self.configuration not in STEREO_BOND_CONFIGURATIONS:
            raise ReactionSchemaError(
                "stereo_double_bond.configuration must be one of "
                f"{sorted(STEREO_BOND_CONFIGURATIONS)}, got {self.configuration!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping."""
        return {"atom_ids": list(self.atom_ids), "configuration": self.configuration}

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], where: str = "stereo_double_bond"
    ) -> StereoDoubleBond:
        """Build one double-bond configuration from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _STEREO_BOND_KEYS, where)
        require_keys(data, _STEREO_BOND_KEYS, where)
        pair = require_sequence(data["atom_ids"], f"{where}.atom_ids")
        if len(pair) != 2:
            raise ReactionSchemaError(f"{where}.atom_ids must contain exactly two ids")
        return cls(
            atom_ids=(
                require_string(pair[0], f"{where}.atom_ids[0]"),
                require_string(pair[1], f"{where}.atom_ids[1]"),
            ),
            configuration=require_string(data["configuration"], f"{where}.configuration"),
        )


@dataclass(frozen=True, slots=True)
class Stereochemistry:
    """Per-species stereochemistry declaration.

    ``status``/``configuration`` may be explicitly ``"unknown"``.  When the
    status is ``unknown`` no assignment may be present; when it is ``defined``
    no member may be explicitly unknown.
    """

    status: str
    centers: tuple[StereoCenter, ...] = ()
    double_bonds: tuple[StereoDoubleBond, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STEREO_STATUSES:
            raise ReactionSchemaError(
                f"stereochemistry.status must be one of {sorted(STEREO_STATUSES)}, "
                f"got {self.status!r}"
            )
        centers = _coerce_centers(self.centers, "stereochemistry.centers")
        double_bonds = _coerce_double_bonds(self.double_bonds, "stereochemistry.double_bonds")
        object.__setattr__(self, "centers", centers)
        object.__setattr__(self, "double_bonds", double_bonds)

        assignments = [center.configuration for center in centers]
        assignments.extend(bond.configuration for bond in double_bonds)
        if self.status == "unknown" and any(value != "unknown" for value in assignments):
            raise InventedStereoError(
                "stereochemistry.status 'unknown' cannot carry an R/S/E/Z assignment"
            )
        if self.status == "defined" and any(value == "unknown" for value in assignments):
            raise StereoContradictionError(
                "stereochemistry.status 'defined' contains an explicitly unknown configuration"
            )
        if self.status == "partially_defined":
            if not assignments or all(value == "unknown" for value in assignments):
                raise StereoContradictionError(
                    "stereochemistry.status 'partially_defined' has no defined configuration"
                )
            if all(value != "unknown" for value in assignments):
                raise StereoContradictionError(
                    "stereochemistry.status 'partially_defined' has no unknown configuration"
                )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping, preserving every unknown marker."""
        return {
            "status": self.status,
            "centers": [center.to_dict() for center in self.centers],
            "double_bonds": [bond.to_dict() for bond in self.double_bonds],
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], where: str = "stereochemistry"
    ) -> Stereochemistry:
        """Build a stereochemistry declaration from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _STEREOCHEMISTRY_KEYS, where)
        require_keys(data, _STEREOCHEMISTRY_KEYS, where)
        return cls(
            status=require_string(data["status"], f"{where}.status"),
            centers=_coerce_centers(data["centers"], f"{where}.centers"),
            double_bonds=_coerce_double_bonds(data["double_bonds"], f"{where}.double_bonds"),
        )


# ── structure reference ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StructureRef:
    """One species on a reaction side, with stable serialized atom identity.

    ``atom_ids`` is the identity; array position is never used to regenerate an
    id.  ``elements[i]`` belongs to ``atom_ids[i]``.
    """

    structure_id: str
    species: str
    formula: str
    input_format: str
    atom_id_scheme: str
    atom_ids: tuple[str, ...]
    elements: tuple[str, ...]
    charge: int
    multiplicity: int
    stereochemistry: Stereochemistry

    def __post_init__(self) -> None:
        require_string(self.structure_id, "structure.structure_id")
        require_string(self.species, "structure.species")
        formula = require_string(self.formula, "structure.formula")
        if not is_formula(formula):
            raise ReactionSchemaError(
                f"structure.formula {formula!r} is not a Hill-order elemental formula"
            )
        if self.input_format not in INPUT_FORMATS:
            raise ReactionSchemaError(
                f"structure.input_format must be one of {sorted(INPUT_FORMATS)}, "
                f"got {self.input_format!r}"
            )
        if self.atom_id_scheme not in ATOM_ID_SCHEMES:
            raise ReactionSchemaError(
                f"structure.atom_id_scheme must be one of {sorted(ATOM_ID_SCHEMES)}, "
                f"got {self.atom_id_scheme!r}"
            )
        atom_ids = _string_tuple(self.atom_ids, "structure.atom_ids")
        elements = tuple(
            require_string(element, f"structure.elements[{index}]")
            for index, element in enumerate(require_sequence(self.elements, "structure.elements"))
        )
        if not elements:
            raise ReactionSchemaError("structure.elements must not be empty")
        if len(elements) != len(atom_ids):
            raise AtomIdentityError(
                f"structure {self.structure_id!r}: elements length {len(elements)} "
                f"!= atom_ids length {len(atom_ids)}"
            )
        for index, element in enumerate(elements):
            is_symbol = (
                len(element) <= 2
                and element[:1].isupper()
                and all(char.islower() for char in element[1:])
            )
            if not is_symbol:
                raise ReactionSchemaError(
                    f"structure.elements[{index}] {element!r} is not an element symbol"
                )
        charge = require_int(self.charge, "structure.charge")
        multiplicity = require_int(self.multiplicity, "structure.multiplicity", minimum=1)
        object.__setattr__(self, "atom_ids", atom_ids)
        object.__setattr__(self, "elements", elements)
        object.__setattr__(self, "charge", charge)
        object.__setattr__(self, "multiplicity", multiplicity)
        object.__setattr__(
            self,
            "stereochemistry",
            _coerce_stereochemistry(self.stereochemistry, "structure.stereochemistry"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping for this structure reference."""
        return {
            "structure_id": self.structure_id,
            "species": self.species,
            "formula": self.formula,
            "input_format": self.input_format,
            "atom_id_scheme": self.atom_id_scheme,
            "atom_ids": list(self.atom_ids),
            "elements": list(self.elements),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "stereochemistry": self.stereochemistry.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "structure") -> StructureRef:
        """Build one structure reference from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _STRUCTURE_KEYS, where)
        require_keys(data, _STRUCTURE_KEYS, where)
        return cls(
            structure_id=require_string(data["structure_id"], f"{where}.structure_id"),
            species=require_string(data["species"], f"{where}.species"),
            formula=require_string(data["formula"], f"{where}.formula"),
            input_format=require_string(data["input_format"], f"{where}.input_format"),
            atom_id_scheme=require_string(data["atom_id_scheme"], f"{where}.atom_id_scheme"),
            atom_ids=_string_tuple(data["atom_ids"], f"{where}.atom_ids"),
            elements=tuple(
                require_string(element, f"{where}.elements[{index}]")
                for index, element in enumerate(
                    require_sequence(data["elements"], f"{where}.elements")
                )
            ),
            charge=require_int(data["charge"], f"{where}.charge"),
            multiplicity=require_int(data["multiplicity"], f"{where}.multiplicity", minimum=1),
            stereochemistry=_coerce_stereochemistry(
                data["stereochemistry"], f"{where}.stereochemistry"
            ),
        )

    def to_structure_artifact(
        self,
        path: str | Path,
        *,
        role: str = "minimum",
        source: str = "",
    ) -> StructureArtifact:
        """Bind this serialized species to the existing execution-layer model.

        ``StructureArtifact`` remains the single structure artifact definition;
        this method only attaches a concrete file path and element order.
        """
        from cccp.calculation.contracts import StructureArtifact, StructureRole

        return StructureArtifact(
            path=Path(path),
            elements=list(self.elements),
            role=StructureRole(role),
            source=source,
        )


# ── atom mapping ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AtomMapping:
    """One-to-one atom mapping with explicit unmapped atoms.

    Both directions must be exact inverses, no atom may be mapped twice and an
    atom may not be both mapped and listed as unmapped.  Dictionary insertion
    order is preserved for canonical serialization but never defines identity.
    """

    left_to_right: dict[str, str]
    right_to_left: dict[str, str]
    unmapped_left: tuple[str, ...] = ()
    unmapped_right: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        left_to_right = {
            require_string(key, "atom_mapping.left_to_right key"): require_string(
                value, f"atom_mapping.left_to_right[{key!r}]"
            )
            for key, value in require_mapping(
                self.left_to_right, "atom_mapping.left_to_right"
            ).items()
        }
        right_to_left = {
            require_string(key, "atom_mapping.right_to_left key"): require_string(
                value, f"atom_mapping.right_to_left[{key!r}]"
            )
            for key, value in require_mapping(
                self.right_to_left, "atom_mapping.right_to_left"
            ).items()
        }
        for address in (*left_to_right, *left_to_right.values()):
            _address_parts(address, "atom_mapping.left_to_right")
        for address in (*right_to_left, *right_to_left.values()):
            _address_parts(address, "atom_mapping.right_to_left")

        right_targets = list(left_to_right.values())
        if len(set(right_targets)) != len(right_targets):
            duplicate = next(
                value for value in right_targets if right_targets.count(value) > 1
            )
            raise DuplicateMapError(
                f"atom_mapping.left_to_right claims {duplicate!r} more than once"
            )
        left_targets = list(right_to_left.values())
        if len(set(left_targets)) != len(left_targets):
            duplicate = next(value for value in left_targets if left_targets.count(value) > 1)
            raise DuplicateMapError(
                f"atom_mapping.right_to_left claims {duplicate!r} more than once"
            )

        expected_inverse = {value: key for key, value in left_to_right.items()}
        if expected_inverse != right_to_left:
            difference = sorted(set(expected_inverse.items()) ^ set(right_to_left.items()))[:3]
            raise MapInverseError(
                "atom_mapping directions are not exact inverses "
                f"(first differences: {difference})"
            )

        unmapped_left = _string_tuple(
            self.unmapped_left, "atom_mapping.unmapped_left", allow_empty=True
        )
        unmapped_right = _string_tuple(
            self.unmapped_right, "atom_mapping.unmapped_right", allow_empty=True
        )
        for address in (*unmapped_left, *unmapped_right):
            _address_parts(address, "atom_mapping.unmapped")
        if set(left_to_right) & set(unmapped_left):
            raise MapCoverageError(
                "atoms both mapped and unmapped on the left: "
                f"{sorted(set(left_to_right) & set(unmapped_left))}"
            )
        if set(right_to_left) & set(unmapped_right):
            raise MapCoverageError(
                "atoms both mapped and unmapped on the right: "
                f"{sorted(set(right_to_left) & set(unmapped_right))}"
            )

        object.__setattr__(self, "left_to_right", left_to_right)
        object.__setattr__(self, "right_to_left", right_to_left)
        object.__setattr__(self, "unmapped_left", unmapped_left)
        object.__setattr__(self, "unmapped_right", unmapped_right)

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping preserving key order."""
        return {
            "left_to_right": dict(self.left_to_right),
            "right_to_left": dict(self.right_to_left),
            "unmapped_left": list(self.unmapped_left),
            "unmapped_right": list(self.unmapped_right),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "atom_mapping") -> AtomMapping:
        """Build an atom mapping from its canonical mapping.

        Duplicate claims and non-inverse directions raise before a valid
        :class:`AtomMapping` instance can be constructed.
        """
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _MAPPING_KEYS, where)
        require_keys(data, _MAPPING_KEYS, where)
        return cls(
            left_to_right=require_mapping(data["left_to_right"], f"{where}.left_to_right"),
            right_to_left=require_mapping(data["right_to_left"], f"{where}.right_to_left"),
            unmapped_left=_string_tuple(
                data["unmapped_left"], f"{where}.unmapped_left", allow_empty=True
            ),
            unmapped_right=_string_tuple(
                data["unmapped_right"], f"{where}.unmapped_right", allow_empty=True
            ),
        )


# ── bond changes ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BondChange:
    """One declared bond change between the two sides.

    ``formed``/``broken``/``changed`` mirror
    ``cccp.analysis.pes.bond_changes`` (``form -> formed``,
    ``break -> broken``, ``order_up|order_down -> changed``).  ``None`` means
    "not supplied", never zero and never an inferred value.
    """

    change_type: str
    left_atoms: tuple[str, str] | None = None
    right_atoms: tuple[str, str] | None = None
    bond_order_left: int | float | None = None
    bond_order_right: int | float | None = None

    def __post_init__(self) -> None:
        if self.change_type not in BOND_CHANGE_TYPES:
            raise BondChangeError(
                f"bond_change.change_type must be one of {sorted(BOND_CHANGE_TYPES)}, "
                f"got {self.change_type!r}"
            )
        for name, value in (
            ("left_atoms", self.left_atoms),
            ("right_atoms", self.right_atoms),
        ):
            if value is not None:
                pair = tuple(value)
                if len(pair) != 2:
                    raise BondChangeError(f"bond_change.{name} must contain exactly two addresses")
                for index, address in enumerate(pair):
                    _address_parts(address, f"bond_change.{name}[{index}]")
                object.__setattr__(self, name, (pair[0], pair[1]))
        if self.left_atoms is None and self.right_atoms is None:
            raise BondChangeError(
                "bond_change must declare at least one side (left_atoms or right_atoms)"
            )
        object.__setattr__(
            self, "bond_order_left", _optional_order(self.bond_order_left, "bond_order_left")
        )
        object.__setattr__(
            self, "bond_order_right", _optional_order(self.bond_order_right, "bond_order_right")
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping."""
        return {
            "change_type": self.change_type,
            "left_atoms": list(self.left_atoms) if self.left_atoms is not None else None,
            "right_atoms": list(self.right_atoms) if self.right_atoms is not None else None,
            "bond_order_left": self.bond_order_left,
            "bond_order_right": self.bond_order_right,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "bond_change") -> BondChange:
        """Build one bond change from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _BOND_CHANGE_KEYS, where)
        require_keys(data, _BOND_CHANGE_KEYS, where)
        left = data["left_atoms"]
        right = data["right_atoms"]
        return cls(
            change_type=require_string(data["change_type"], f"{where}.change_type"),
            left_atoms=_optional_pair(left, f"{where}.left_atoms"),
            right_atoms=_optional_pair(right, f"{where}.right_atoms"),
            bond_order_left=_optional_order(data["bond_order_left"], f"{where}.bond_order_left"),
            bond_order_right=_optional_order(
                data["bond_order_right"], f"{where}.bond_order_right"
            ),
        )


def _optional_pair(value: Any, where: str) -> tuple[str, str] | None:
    if value is None:
        return None
    pair = require_sequence(value, where)
    if len(pair) != 2:
        raise BondChangeError(f"{where}: must contain exactly two atom addresses")
    return (require_string(pair[0], f"{where}[0]"), require_string(pair[1], f"{where}[1]"))


# ── conditions and uncertainty ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Conditions:
    """Explicit thermodynamic conditions; ``None`` means "not supplied"."""

    temperature_k: int | float | None
    pressure_pa: int | float | None
    standard_state: str | None
    solvent: str | None
    phase: str | None

    def __post_init__(self) -> None:
        if self.temperature_k is not None:
            require_number(self.temperature_k, "conditions.temperature_k", minimum=0.0)
        if self.pressure_pa is not None:
            require_number(self.pressure_pa, "conditions.pressure_pa", minimum=0.0)
        if self.standard_state is not None and self.standard_state not in STANDARD_STATES:
            raise ReactionSchemaError(
                f"conditions.standard_state must be one of {sorted(STANDARD_STATES)} or null, "
                f"got {self.standard_state!r}"
            )
        if self.phase is not None and self.phase not in PHASES:
            raise ReactionSchemaError(
                f"conditions.phase must be one of {sorted(PHASES)} or null, got {self.phase!r}"
            )
        if self.solvent is not None:
            require_string(self.solvent, "conditions.solvent")

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping preserving every null."""
        return {
            "temperature_k": self.temperature_k,
            "pressure_pa": self.pressure_pa,
            "standard_state": self.standard_state,
            "solvent": self.solvent,
            "phase": self.phase,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "conditions") -> Conditions:
        """Build conditions from their canonical mapping."""
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _CONDITION_KEYS, where)
        require_keys(data, _CONDITION_KEYS, where)
        return cls(
            temperature_k=_optional_number(data["temperature_k"], f"{where}.temperature_k"),
            pressure_pa=_optional_number(data["pressure_pa"], f"{where}.pressure_pa"),
            standard_state=_optional_string(
                data["standard_state"], f"{where}.standard_state"
            ),
            solvent=_optional_string(data["solvent"], f"{where}.solvent"),
            phase=_optional_string(data["phase"], f"{where}.phase"),
        )

    def null_paths(self) -> tuple[str, ...]:
        """Return dotted paths whose value is ``None``."""
        return tuple(
            f"conditions.{name}"
            for name in ("temperature_k", "pressure_pa", "standard_state", "solvent", "phase")
            if getattr(self, name) is None
        )


def _optional_number(value: Any, where: str) -> int | float | None:
    if value is None:
        return None
    return require_number(value, where, minimum=0.0)


def _optional_string(value: Any, where: str, *, allow_empty: bool = False) -> str | None:
    if value is None:
        return None
    return require_string(value, where, allow_empty=allow_empty)


@dataclass(frozen=True, slots=True)
class InputUncertainty:
    """Declared omissions; a missing scientific value must be listed here."""

    unknown_fields: tuple[str, ...] = ()
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "unknown_fields",
            _string_tuple(self.unknown_fields, "input_uncertainty.unknown_fields", allow_empty=True),
        )
        if self.notes is not None:
            require_string(self.notes, "input_uncertainty.notes", allow_empty=True)

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping."""
        return {"unknown_fields": list(self.unknown_fields), "notes": self.notes}

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], where: str = "input_uncertainty"
    ) -> InputUncertainty:
        """Build the uncertainty declaration from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_forbidden_keys(data, where)
        reject_unknown_keys(data, _UNCERTAINTY_KEYS, where)
        require_keys(data, _UNCERTAINTY_KEYS, where)
        notes = data["notes"]
        if notes is not None:
            require_string(notes, f"{where}.notes", allow_empty=True)
        return cls(
            unknown_fields=_string_tuple(
                data["unknown_fields"], f"{where}.unknown_fields", allow_empty=True
            ),
            notes=notes,
        )


# ── reaction ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Reaction:
    """Frozen ACP2 reaction contract (``reaction.schema.json`` v1)."""

    reaction_id: str
    reaction_scope: Literal["net_reaction", "elementary_step"]
    left: tuple[StructureRef, ...]
    right: tuple[StructureRef, ...]
    atom_mapping: AtomMapping
    bond_changes: tuple[BondChange, ...]
    conditions: Conditions
    input_uncertainty: InputUncertainty
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                f"unsupported reaction schema_version {self.schema_version!r}; "
                f"only {SCHEMA_VERSION} is readable as a canonical reaction"
            )
        require_string(self.reaction_id, "reaction.reaction_id")
        if self.reaction_scope not in REACTION_SCOPES:
            raise ReactionSchemaError(
                f"reaction.reaction_scope must be one of {sorted(REACTION_SCOPES)}, "
                f"got {self.reaction_scope!r}"
            )
        left = _coerce_structures(self.left, "left")
        right = _coerce_structures(self.right, "right")
        if not left:
            raise MissingReactantError("reaction left side is empty; at least one species is required")
        if not right:
            raise MissingProductError(
                "reaction right side is empty; a transformation needs at least one product"
            )
        atom_mapping = (
            self.atom_mapping
            if isinstance(self.atom_mapping, AtomMapping)
            else AtomMapping.from_dict(require_mapping(self.atom_mapping, "atom_mapping"))
        )
        bond_changes = tuple(
            change
            if isinstance(change, BondChange)
            else BondChange.from_dict(
                require_mapping(change, f"bond_changes[{index}]"), f"bond_changes[{index}]"
            )
            for index, change in enumerate(require_sequence(self.bond_changes, "bond_changes"))
        )
        conditions = (
            self.conditions
            if isinstance(self.conditions, Conditions)
            else Conditions.from_dict(require_mapping(self.conditions, "conditions"))
        )
        uncertainty = (
            self.input_uncertainty
            if isinstance(self.input_uncertainty, InputUncertainty)
            else InputUncertainty.from_dict(
                require_mapping(self.input_uncertainty, "input_uncertainty")
            )
        )
        object.__setattr__(self, "left", left)
        object.__setattr__(self, "right", right)
        object.__setattr__(self, "atom_mapping", atom_mapping)
        object.__setattr__(self, "bond_changes", bond_changes)
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "input_uncertainty", uncertainty)

        left_addresses = _structure_addresses(left, "left")
        right_addresses = _structure_addresses(right, "right")
        self._validate_mapping_coverage(left_addresses, right_addresses)
        self._validate_bond_change_addresses(left_addresses, right_addresses)
        self._validate_unknown_declarations(conditions, uncertainty)

    def _validate_mapping_coverage(
        self, left_addresses: set[str], right_addresses: set[str]
    ) -> None:
        mapped_left = set(self.atom_mapping.left_to_right)
        mapped_right = set(self.atom_mapping.right_to_left)
        unmapped_left = set(self.atom_mapping.unmapped_left)
        unmapped_right = set(self.atom_mapping.unmapped_right)

        stray_left = (mapped_left | unmapped_left) - left_addresses
        stray_right = (mapped_right | unmapped_right) - right_addresses
        if stray_left or stray_right:
            raise AtomIdentityError(
                "atom_mapping addresses are not declared on their side: "
                f"left={sorted(stray_left)} right={sorted(stray_right)}"
            )
        missing_left = left_addresses - mapped_left - unmapped_left
        missing_right = right_addresses - mapped_right - unmapped_right
        if missing_left or missing_right:
            raise MapCoverageError(
                "unaccounted atoms not mapped or declared unmapped: "
                f"left={sorted(missing_left)} right={sorted(missing_right)}"
            )

    def _validate_bond_change_addresses(
        self, left_addresses: set[str], right_addresses: set[str]
    ) -> None:
        for index, change in enumerate(self.bond_changes):
            if change.left_atoms is not None:
                stray = set(change.left_atoms) - left_addresses
                if stray:
                    raise AtomIdentityError(
                        f"bond_changes[{index}].left_atoms not on the left side: {sorted(stray)}"
                    )
            if change.right_atoms is not None:
                stray = set(change.right_atoms) - right_addresses
                if stray:
                    raise AtomIdentityError(
                        f"bond_changes[{index}].right_atoms not on the right side: {sorted(stray)}"
                    )

    def _validate_unknown_declarations(
        self, conditions: Conditions, uncertainty: InputUncertainty
    ) -> None:
        declared = set(uncertainty.unknown_fields)
        for path in conditions.null_paths():
            if path not in declared:
                raise UnknownDeclarationError(
                    f"{path} is null but is not declared in input_uncertainty.unknown_fields"
                )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical schema-shaped mapping for this reaction."""
        return {
            "schema_version": self.schema_version,
            "reaction_id": self.reaction_id,
            "reaction_scope": self.reaction_scope,
            "left": [structure.to_dict() for structure in self.left],
            "right": [structure.to_dict() for structure in self.right],
            "atom_mapping": self.atom_mapping.to_dict(),
            "bond_changes": [change.to_dict() for change in self.bond_changes],
            "conditions": self.conditions.to_dict(),
            "input_uncertainty": self.input_uncertainty.to_dict(),
        }

    def to_json(self) -> str:
        """Return the canonical JSON text (sorted keys, stable whitespace)."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Reaction:
        """Build a reaction from canonical v1 JSON, rejecting unknown majors."""
        data = require_mapping(payload, "reaction")
        reject_forbidden_keys(data, "reaction")
        version = read_schema_version(data, "reaction")
        if version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersionError(
                f"unsupported reaction schema_version {version!r}; only {SCHEMA_VERSION} "
                "is readable as a canonical reaction (unknown majors are never coerced)"
            )
        reject_unknown_keys(data, _REACTION_KEYS, "reaction")
        require_keys(data, _REACTION_KEYS, "reaction")
        left = tuple(
            StructureRef.from_dict(item, f"left[{index}]")
            for index, item in enumerate(require_sequence(data["left"], "reaction.left"))
        )
        right = tuple(
            StructureRef.from_dict(item, f"right[{index}]")
            for index, item in enumerate(require_sequence(data["right"], "reaction.right"))
        )
        bond_changes = tuple(
            BondChange.from_dict(item, f"bond_changes[{index}]")
            for index, item in enumerate(
                require_sequence(data["bond_changes"], "reaction.bond_changes")
            )
        )
        return cls(
            schema_version=version,
            reaction_id=require_string(data["reaction_id"], "reaction.reaction_id"),
            reaction_scope=require_string(data["reaction_scope"], "reaction.reaction_scope"),
            left=left,
            right=right,
            atom_mapping=AtomMapping.from_dict(
                require_mapping(data["atom_mapping"], "reaction.atom_mapping")
            ),
            bond_changes=bond_changes,
            conditions=Conditions.from_dict(
                require_mapping(data["conditions"], "reaction.conditions")
            ),
            input_uncertainty=InputUncertainty.from_dict(
                require_mapping(data["input_uncertainty"], "reaction.input_uncertainty")
            ),
        )

    # NOTE: ``ReactionForbiddenFieldError`` is raised by ``from_dict`` via
    # ``reject_forbidden_keys``; the other typed errors above are raised by the
    # dataclass invariants.  ``ReactionSchemaError`` is the generic structural
    # error for shapes/regex/array-length violations.


def _coerce_structures(value: Any, where: str) -> tuple[StructureRef, ...]:
    items = require_sequence(value, where)
    return tuple(
        item
        if isinstance(item, StructureRef)
        else StructureRef.from_dict(require_mapping(item, f"{where}[{index}]"), f"{where}[{index}]")
        for index, item in enumerate(items)
    )


def _structure_addresses(structures: tuple[StructureRef, ...], where: str) -> set[str]:
    seen_ids: set[str] = set()
    addresses: set[str] = set()
    for structure in structures:
        if structure.structure_id in seen_ids:
            raise AtomIdentityError(
                f"{where}: duplicate structure_id {structure.structure_id!r} on one side"
            )
        seen_ids.add(structure.structure_id)
        for atom_id in structure.atom_ids:
            addresses.add(f"{structure.structure_id}:{atom_id}")
    return addresses


# ── historical compatibility ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HistoricalReactionRecord:
    """A readable historical payload that is never execution/recovery eligible.

    Unknown fields (including nested ones) are preserved verbatim in
    :attr:`payload`; nothing is defaulted or upgraded.  Tolerating historical
    fields deliberately does not grant mutation/execution eligibility, mirroring
    the evidence adapter used by A-03.
    """

    payload: dict[str, Any]
    schema_version: int | None
    adapter: str = "historical_reaction_v1"
    execution_eligible: bool = False
    recovery_eligible: bool = False

    @property
    def unknown_fields(self) -> tuple[str, ...]:
        """Top-level fields not declared by the canonical v1 reaction contract."""
        return tuple(sorted(set(self.payload) - _REACTION_KEYS))

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the preserved payload (byte-stable re-read)."""
        return copy.deepcopy(self.payload)


def read_historical_reaction(
    payload: Mapping[str, Any], *, schema_version: int | None = None
) -> HistoricalReactionRecord:
    """Read a historical reaction payload without canonical coercion.

    Unknown (non-forbidden) fields stay in the returned record instead of being
    dropped or filled with defaults.  The record is explicitly not eligible for
    execution or recovery; use :meth:`Reaction.from_dict` for new work.
    """
    data = require_mapping(payload, "historical_reaction")
    try:
        reject_forbidden_keys(data, "historical_reaction")
    except ForbiddenPlatformFieldError as exc:
        raise ReactionForbiddenFieldError(str(exc)) from exc
    declared = schema_version
    if declared is None and "schema_version" in data:
        raw = data["schema_version"]
        if isinstance(raw, int) and not isinstance(raw, bool):
            declared = raw
    return HistoricalReactionRecord(
        payload=copy.deepcopy(data),
        schema_version=declared,
        execution_eligible=False,
        recovery_eligible=False,
    )
