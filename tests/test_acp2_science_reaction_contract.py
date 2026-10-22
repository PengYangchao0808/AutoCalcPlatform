# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""ACP2 reaction/question contract semantics (Plan A todo 6 / A-02b).

The frozen JSON Schemas in
``docs/reference/next-change/acp2-contracts/science/`` describe structure only.
This module implements the **scientific-semantics** rules named in
``semantic-rules.md`` with the Python standard library alone: no ``jsonschema``,
no third-party schema engine and no production import. Every negative fixture
must fail with its own named error class, never a generic exception.

Rules exercised here:

* R-01 stable atom ids and resolvable canonical atom addresses;
* R-02 one-to-one atom maps (exact inverses, no duplicate claim, full coverage);
* R-03 element/stoichiometry and net-charge conservation plus multiplicity
  parity against the species electron count;
* R-04 explicit unknown stereochemistry and conditions are preserved, never
  invented;
* R-06 a net reaction is never relabelled as one elementary step without
  evidence outside this contract;
* R-08 the activation-energy question carries no role/cost/price/job status.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
SEMANTIC_RULES = SCIENCE / "semantic-rules.md"
EXAMPLES = SCIENCE / "examples"
REACTION_FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "contracts" / "reaction"
CONTRACT_FIXTURES = REACTION_FIXTURES.parent

FORBIDDEN_QUESTION_FIELDS = (
    "role",
    "model_role",
    "cost",
    "price",
    "job_status",
    "queue",
    "account",
)
CLAIM_STATUSES = ["supported", "refuted", "inconclusive", "not_evaluated"]
EVIDENCE_TYPES = {
    "transition_state_geometry",
    "imaginary_frequency",
    "irc_forward",
    "irc_reverse",
    "stationary_point_gradient",
    "path_endpoints",
    "energy_difference",
    "thermochemistry",
}
COMPATIBILITY_REQUIREMENTS = {
    "same_method",
    "same_electronic_state",
    "same_temperature",
    "same_pressure",
    "same_standard_state",
    "same_reference_state",
    "same_energy_unit",
}
ENERGY_UNITS = {"hartree", "kcal_per_mol", "kj_per_mol"}
STANDARD_STATES = {"1_bar_ideal_gas", "1_molar_ideal_solution", "unknown", None}

# Atomic numbers through Kr — enough for the reference fixtures and the
# multiplicity-parity check without adding a dependency.
_ATOMIC_NUMBER = {
    "H": 1,
    "He": 2,
    "Li": 3,
    "Be": 4,
    "B": 5,
    "C": 6,
    "N": 7,
    "O": 8,
    "F": 9,
    "Ne": 10,
    "Na": 11,
    "Mg": 12,
    "Al": 13,
    "Si": 14,
    "P": 15,
    "S": 16,
    "Cl": 17,
    "Ar": 18,
    "K": 19,
    "Ca": 20,
    "Sc": 21,
    "Ti": 22,
    "V": 23,
    "Cr": 24,
    "Mn": 25,
    "Fe": 26,
    "Co": 27,
    "Ni": 28,
    "Cu": 29,
    "Zn": 30,
    "Ga": 31,
    "Ge": 32,
    "As": 33,
    "Se": 34,
    "Br": 35,
    "Kr": 36,
}


# ── named semantic errors ───────────────────────────────────────────────────


class ReactionContractError(ValueError):
    """Base class for a rejected reaction payload."""


class SchemaVersionError(ReactionContractError):
    """The payload declares an unknown reaction contract major version."""


class MissingReactantError(ReactionContractError):
    """The left side is absent or empty."""


class MissingProductError(ReactionContractError):
    """The right side is absent or empty — an incomplete transformation."""


class AtomIdentityError(ReactionContractError):
    """An atom id is duplicated, or a mapping address does not resolve."""


class DuplicateMapError(ReactionContractError):
    """One atom is claimed more than once by the same mapping direction."""


class MapInverseError(ReactionContractError):
    """left_to_right and right_to_left are not exact inverses."""


class MapCoverageError(ReactionContractError):
    """An atom is mapped and unmapped, or omitted entirely."""


class ElementConservationError(ReactionContractError):
    """Elements or the declared formula do not balance across the reaction."""


class ChargeConservationError(ReactionContractError):
    """Net charge differs between the two sides."""


class MultiplicityConsistencyError(ReactionContractError):
    """Multiplicity parity is inconsistent with the species electron count."""


class UnknownNotDeclaredError(ReactionContractError):
    """A null condition is missing from input_uncertainty.unknown_fields."""


class InventedStereoError(ReactionContractError):
    """A stereochemical assignment contradicts a declared/unknown input fact."""


class StereoContradictionError(ReactionContractError):
    """A stereochemistry status contradicts its own centers/double bonds."""


class BondChangeError(ReactionContractError):
    """A bond change is malformed for its declared change type."""


class ElementaryStepClaimWithoutEvidenceError(ReactionContractError):
    """An elementary-step claim has no supporting evidence in this contract."""


class QuestionForbiddenFieldError(ReactionContractError):
    """The question carries a platform/model field it must never carry."""


class UnknownDeclarationError(ReactionContractError):
    """A declared-unknown path does not actually point at a missing value."""


# ── canonical JSON ──────────────────────────────────────────────────────────


def canonical(payload: Any) -> str:
    """Return the frozen deterministic JSON encoding (2-space, sorted, newline)."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document: dict[str, Any] = json.load(handle)
    assert isinstance(document, dict), f"{path} root must be a JSON object"
    return document


# ── shared helpers ──────────────────────────────────────────────────────────


def _require(document: dict[str, Any], key: str, where: str) -> Any:
    assert key in document, f"{where}: missing required field {key!r}"
    return document[key]


def _hill_formula(elements: list[str]) -> str:
    counts = Counter(elements)
    if "C" in counts:
        ordered = ["C"]
        if "H" in counts:
            ordered.append("H")
        ordered.extend(sorted(symbol for symbol in counts if symbol not in {"C", "H"}))
    else:
        ordered = sorted(counts)
    return "".join(
        f"{symbol}{counts[symbol] if counts[symbol] > 1 else ''}" for symbol in ordered
    )


def _electrons(elements: list[str], charge: int, where: str) -> int:
    total = 0
    for symbol in elements:
        if symbol not in _ATOMIC_NUMBER:
            raise AtomIdentityError(f"{where}: unknown element symbol {symbol!r}")
        total += _ATOMIC_NUMBER[symbol]
    return total - charge


def _side_atoms(side: list[dict[str, Any]], where: str) -> set[str]:
    """Return every canonical ``structure_id:atom_id`` address on one side."""
    addresses: set[str] = set()
    structure_ids: set[str] = set()
    for index, item in enumerate(side):
        structure_id = _require(item, "structure_id", f"{where}[{index}]")
        assert isinstance(structure_id, str) and structure_id
        if structure_id in structure_ids:
            raise AtomIdentityError(
                f"{where}: duplicate structure_id {structure_id!r} on one side"
            )
        structure_ids.add(structure_id)
        atom_ids = _require(item, "atom_ids", f"{where}[{index}]")
        assert isinstance(atom_ids, list) and atom_ids, (
            f"{where}[{index}]: atom_ids must be a non-empty list"
        )
        if len(set(atom_ids)) != len(atom_ids):
            raise AtomIdentityError(
                f"{where}[{index}]: duplicate atom id inside structure {structure_id!r}"
            )
        for atom_id in atom_ids:
            addresses.add(f"{structure_id}:{atom_id}")
    return addresses


def _validate_structure_structure(item: dict[str, Any], where: str) -> None:
    for key in (
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
    ):
        _require(item, key, where)
    assert item["input_format"] in {"mol", "sdf", "smiles", "structure_upload"}, (
        f"{where}: unsupported input_format {item['input_format']!r}"
    )
    assert item["atom_id_scheme"] in {"explicit", "input_order_0_based"}, (
        f"{where}: unsupported atom_id_scheme {item['atom_id_scheme']!r}"
    )
    atom_ids = item["atom_ids"]
    elements = item["elements"]
    assert isinstance(atom_ids, list) and atom_ids, f"{where}: empty atom_ids"
    assert isinstance(elements, list), f"{where}: elements must be a list"
    if len(elements) != len(atom_ids):
        raise AtomIdentityError(
            f"{where}: elements length {len(elements)} != atom_ids length {len(atom_ids)}"
        )
    if len(set(atom_ids)) != len(atom_ids):
        raise AtomIdentityError(f"{where}: duplicate atom id")


def _validate_stereo(
    stereo: dict[str, Any], atom_ids: list[str], unknown_fields: list[str], where: str
) -> None:
    status = _require(stereo, "status", where)
    centers = _require(stereo, "centers", where)
    double_bonds = _require(stereo, "double_bonds", where)
    assert status in {"defined", "partially_defined", "unknown"}, (
        f"{where}: invalid stereochemistry status {status!r}"
    )

    assignments: list[str] = []
    for index, center in enumerate(centers):
        atom_id = _require(center, "atom_id", f"{where}.centers[{index}]")
        if atom_id not in atom_ids:
            raise AtomIdentityError(
                f"{where}.centers[{index}]: atom_id {atom_id!r} is not declared"
            )
        configuration = _require(center, "configuration", f"{where}.centers[{index}]")
        assert configuration in {"R", "S", "unknown"}, (
            f"{where}.centers[{index}]: invalid configuration {configuration!r}"
        )
        declared = f"{where}.centers[{index}].configuration" in unknown_fields
        if declared and configuration != "unknown":
            raise InventedStereoError(
                f"{where}.centers[{index}]: claims {configuration!r} while the "
                "input declares this center unknown"
            )
        assignments.append(configuration)

    for index, double_bond in enumerate(double_bonds):
        pair = _require(double_bond, "atom_ids", f"{where}.double_bonds[{index}]")
        assert isinstance(pair, list) and len(pair) == 2, (
            f"{where}.double_bonds[{index}]: atom_ids must contain exactly two ids"
        )
        for atom_id in pair:
            if atom_id not in atom_ids:
                raise AtomIdentityError(
                    f"{where}.double_bonds[{index}]: atom_id {atom_id!r} is not declared"
                )
        configuration = _require(
            double_bond, "configuration", f"{where}.double_bonds[{index}]"
        )
        assert configuration in {"E", "Z", "unknown"}, (
            f"{where}.double_bonds[{index}]: invalid configuration {configuration!r}"
        )
        declared = f"{where}.double_bonds[{index}].configuration" in unknown_fields
        if declared and configuration != "unknown":
            raise InventedStereoError(
                f"{where}.double_bonds[{index}]: claims {configuration!r} while the "
                "input declares this double bond unknown"
            )
        assignments.append(configuration)

    if f"{where}.status" in unknown_fields and status != "unknown":
        raise InventedStereoError(
            f"{where}: claims status {status!r} while the input declares the "
            "stereochemistry unknown"
        )
    if status == "unknown" and any(value != "unknown" for value in assignments):
        raise InventedStereoError(
            f"{where}: status 'unknown' cannot carry an R/S/E/Z assignment"
        )
    if status == "defined" and any(value == "unknown" for value in assignments):
        raise StereoContradictionError(
            f"{where}: status 'defined' contains an explicitly unknown configuration"
        )
    if status == "partially_defined":
        if not assignments or all(value == "unknown" for value in assignments):
            raise StereoContradictionError(
                f"{where}: status 'partially_defined' has no defined configuration"
            )
        if all(value != "unknown" for value in assignments):
            raise StereoContradictionError(
                f"{where}: status 'partially_defined' has no unknown configuration"
            )


def _resolve(address: str, side: list[dict[str, Any]], where: str) -> str:
    if not isinstance(address, str) or ":" not in address:
        raise AtomIdentityError(f"{where}: malformed atom address {address!r}")
    structure_id, _, atom_id = address.partition(":")
    for item in side:
        if item["structure_id"] == structure_id:
            if atom_id not in item["atom_ids"]:
                raise AtomIdentityError(
                    f"{where}: atom id {atom_id!r} is not declared in {structure_id!r}"
                )
            return address
    raise AtomIdentityError(
        f"{where}: structure id {structure_id!r} does not exist on this side"
    )


def _check_one_to_one(
    mapping: dict[str, Any],
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> None:
    left_addresses = _side_atoms(left, "left")
    right_addresses = _side_atoms(right, "right")
    left_to_right = _require(mapping, "left_to_right", "atom_mapping")
    right_to_left = _require(mapping, "right_to_left", "atom_mapping")
    unmapped_left = _require(mapping, "unmapped_left", "atom_mapping")
    unmapped_right = _require(mapping, "unmapped_right", "atom_mapping")

    for left_address, right_address in left_to_right.items():
        _resolve(left_address, left, "atom_mapping.left_to_right")
        _resolve(right_address, right, "atom_mapping.left_to_right")
    for right_address, left_address in right_to_left.items():
        _resolve(right_address, right, "atom_mapping.right_to_left")
        _resolve(left_address, left, "atom_mapping.right_to_left")
    for address in unmapped_left:
        _resolve(address, left, "atom_mapping.unmapped_left")
    for address in unmapped_right:
        _resolve(address, right, "atom_mapping.unmapped_right")

    right_targets = list(left_to_right.values())
    if len(set(right_targets)) != len(right_targets):
        duplicate = next(
            value for value, count in Counter(right_targets).items() if count > 1
        )
        raise DuplicateMapError(
            f"atom_mapping.left_to_right claims {duplicate!r} more than once"
        )
    left_targets = list(right_to_left.values())
    if len(set(left_targets)) != len(left_targets):
        duplicate = next(
            value for value, count in Counter(left_targets).items() if count > 1
        )
        raise DuplicateMapError(
            f"atom_mapping.right_to_left claims {duplicate!r} more than once"
        )

    expected_inverse = {value: key for key, value in left_to_right.items()}
    if expected_inverse != right_to_left:
        missing = sorted(set(expected_inverse.items()) ^ set(right_to_left.items()))[:3]
        raise MapInverseError(
            "left_to_right and right_to_left are not exact inverses "
            f"(first differences: {missing})"
        )

    mapped_left = set(left_to_right)
    mapped_right = set(right_to_left)
    unmapped_left_set = set(unmapped_left)
    unmapped_right_set = set(unmapped_right)
    overlap_left = mapped_left & unmapped_left_set
    overlap_right = mapped_right & unmapped_right_set
    if overlap_left or overlap_right:
        raise MapCoverageError(
            f"atoms both mapped and unmapped: left={sorted(overlap_left)} "
            f"right={sorted(overlap_right)}"
        )
    missing_left = left_addresses - mapped_left - unmapped_left_set
    missing_right = right_addresses - mapped_right - unmapped_right_set
    if missing_left or missing_right:
        raise MapCoverageError(
            f"unaccounted atoms: left={sorted(missing_left)} right={sorted(missing_right)}"
        )
    stray_left = (mapped_left | unmapped_left_set) - left_addresses
    stray_right = (mapped_right | unmapped_right_set) - right_addresses
    if stray_left or stray_right:
        raise AtomIdentityError(
            f"atom addresses not on their side: left={sorted(stray_left)} "
            f"right={sorted(stray_right)}"
        )


def _check_bond_changes(bond_changes: list[dict[str, Any]]) -> None:
    assert isinstance(bond_changes, list), "bond_changes must be a list"
    pairs: set[frozenset[str]] = set()
    for index, change in enumerate(bond_changes):
        where = f"bond_changes[{index}]"
        change_type = _require(change, "change_type", where)
        assert change_type in {"formed", "broken", "changed"}, (
            f"{where}: invalid change_type {change_type!r}"
        )
        left_atoms = change.get("left_atoms")
        right_atoms = change.get("right_atoms")
        order_left = change.get("bond_order_left")
        order_right = change.get("bond_order_right")
        if left_atoms is not None:
            assert isinstance(left_atoms, list) and len(left_atoms) == 2, (
                f"{where}: left_atoms must contain exactly two addresses"
            )
        if right_atoms is not None:
            assert isinstance(right_atoms, list) and len(right_atoms) == 2, (
                f"{where}: right_atoms must contain exactly two addresses"
            )
        if change_type == "formed":
            if order_left not in (0, 0.0) or order_right in (None, 0, 0.0):
                raise BondChangeError(f"{where}: formed requires 0 -> >0 bond orders")
        elif change_type == "broken":
            if order_left in (None, 0, 0.0) or order_right not in (0, 0.0):
                raise BondChangeError(f"{where}: broken requires >0 -> 0 bond orders")
        else:
            if order_left is None or order_right is None or order_left == order_right:
                raise BondChangeError(
                    f"{where}: changed requires two distinct non-null bond orders"
                )
        if left_atoms is not None:
            pairs.add(frozenset(left_atoms))
        if right_atoms is not None:
            pairs.add(frozenset(right_atoms))
    # At least one declared change is required for a contract-visible reaction.
    assert bond_changes, "bond_changes must not be empty"


def _side_totals(
    side: list[dict[str, Any]], label: str
) -> tuple[Counter[str], int, list[tuple[str, list[str], int, int]]]:
    """Aggregate one side and defer per-species multiplicity parity.

    Cross-side conservation (elements, then net charge) is checked before the
    per-species electron-count parity so a charge defect is reported as a charge
    defect and an element defect as an element defect. Otherwise a single flipped
    charge would masquerade as a multiplicity error.
    """
    elements: Counter[str] = Counter()
    charge = 0
    parity_checks: list[tuple[str, list[str], int, int]] = []
    for index, item in enumerate(side):
        where = f"{label}[{index}]"
        _validate_structure_structure(item, where)
        item_elements = item["elements"]
        declared_formula = item["formula"]
        computed = _hill_formula(item_elements)
        if declared_formula != computed:
            raise ElementConservationError(
                f"{where}: formula {declared_formula!r} != element list {computed!r}"
            )
        if len(item["atom_ids"]) != len(item_elements):
            raise AtomIdentityError(f"{where}: atom_ids/elements length mismatch")
        charge_value = item["charge"]
        assert isinstance(charge_value, int) and not isinstance(charge_value, bool)
        multiplicity = item["multiplicity"]
        assert isinstance(multiplicity, int) and not isinstance(multiplicity, bool)
        assert multiplicity >= 1, f"{where}: multiplicity must be >= 1"
        parity_checks.append((where, item_elements, charge_value, multiplicity))
        elements.update(item_elements)
        charge += charge_value
    return elements, charge, parity_checks


# ── reaction validator ──────────────────────────────────────────────────────


def validate_reaction(document: dict[str, Any]) -> None:
    """Validate one reaction payload against the frozen v1 semantic rules.

    Raises a named :class:`ReactionContractError` subclass on the first
    violation. Returns ``None`` when the payload is scientifically coherent.
    """
    assert isinstance(document, dict), "reaction root must be a JSON object"
    if _require(document, "schema_version", "reaction") != 1:
        raise SchemaVersionError(
            f"unsupported reaction schema_version {document['schema_version']!r}"
        )
    for key in (
        "reaction_id",
        "reaction_scope",
        "left",
        "right",
        "atom_mapping",
        "bond_changes",
        "conditions",
        "input_uncertainty",
    ):
        _require(document, key, "reaction")
    for forbidden in FORBIDDEN_QUESTION_FIELDS:
        if forbidden in document:
            raise ReactionContractError(
                f"reaction carries forbidden platform/model field {forbidden!r}"
            )

    scope = document["reaction_scope"]
    assert scope in {"net_reaction", "elementary_step"}, (
        f"invalid reaction_scope {scope!r}"
    )

    left = document["left"]
    right = document["right"]
    assert isinstance(left, list), "left must be a list"
    assert isinstance(right, list), "right must be a list"
    if not left:
        raise MissingReactantError("reaction right side is present but left side is empty")
    if not right:
        raise MissingProductError(
            "reaction right/product side is empty; a transformation needs a product"
        )

    input_uncertainty = document["input_uncertainty"]
    unknown_fields = _require(input_uncertainty, "unknown_fields", "input_uncertainty")
    _require(input_uncertainty, "notes", "input_uncertainty")
    assert isinstance(unknown_fields, list), "unknown_fields must be a list"

    left_elements, left_charge, left_parity = _side_totals(left, "left")
    right_elements, right_charge, right_parity = _side_totals(right, "right")

    for index, item in enumerate(left):
        _validate_stereo(
            item["stereochemistry"], item["atom_ids"], unknown_fields, f"left[{index}].stereochemistry"
        )
    for index, item in enumerate(right):
        _validate_stereo(
            item["stereochemistry"],
            item["atom_ids"],
            unknown_fields,
            f"right[{index}].stereochemistry",
        )

    conditions = document["conditions"]
    for key in ("temperature_k", "pressure_pa", "standard_state", "solvent", "phase"):
        _require(conditions, key, "conditions")
    for key, value in conditions.items():
        if value is None and f"conditions.{key}" not in unknown_fields:
            raise UnknownNotDeclaredError(
                f"conditions.{key} is null but not declared in "
                "input_uncertainty.unknown_fields"
            )
    if conditions["standard_state"] not in STANDARD_STATES:
        raise UnknownNotDeclaredError(
            f"conditions.standard_state {conditions['standard_state']!r} is invalid"
        )
    if conditions["phase"] not in {"gas", "solution", "liquid", "solid", "unknown", None}:
        raise UnknownNotDeclaredError(
            f"conditions.phase {conditions['phase']!r} is invalid"
        )

    _check_one_to_one(document["atom_mapping"], left, right)

    if left_elements != right_elements:
        raise ElementConservationError(
            f"element counts differ: left={dict(sorted(left_elements.items()))} "
            f"right={dict(sorted(right_elements.items()))}"
        )
    if left_charge != right_charge:
        raise ChargeConservationError(
            f"net charge differs: left={left_charge:+d} right={right_charge:+d}"
        )

    for where, item_elements, charge_value, multiplicity in (*left_parity, *right_parity):
        electrons = _electrons(item_elements, charge_value, where)
        if (multiplicity % 2) != ((electrons + 1) % 2):
            raise MultiplicityConsistencyError(
                f"{where}: multiplicity {multiplicity} is inconsistent with "
                f"{electrons} electrons (parity mismatch)"
            )

    _check_bond_changes(document["bond_changes"])

    # R-06: the frozen v1 schema has no field carrying transition-state/IRC
    # evidence, so an elementary-step claim cannot be substantiated here. A net
    # reaction must never be relabelled as one elementary step.
    if scope == "elementary_step":
        raise ElementaryStepClaimWithoutEvidenceError(
            "reaction_scope 'elementary_step' has no transition-state/IRC evidence "
            "in reaction contract v1; a net reaction must not be claimed as one "
            "elementary step"
        )


# ── question validator ──────────────────────────────────────────────────────


def _walk_forbidden(value: Any, path: str = "question") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_QUESTION_FIELDS:
                raise QuestionForbiddenFieldError(
                    f"{path}: forbidden question field {key!r}"
                )
            _walk_forbidden(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden(child, f"{path}[{index}]")


def _resolve_path(document: dict[str, Any], dotted: str) -> Any:
    current: Any = document
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


_MISSING = object()


def validate_question(document: dict[str, Any]) -> None:
    """Validate one question payload and reject platform/model fields."""
    assert isinstance(document, dict), "question root must be a JSON object"
    _walk_forbidden(document)
    if _require(document, "schema_version", "question") != 1:
        raise SchemaVersionError(
            f"unsupported question schema_version {document['schema_version']!r}"
        )
    for key in (
        "question_id",
        "question_type",
        "hypothesis_refs",
        "required_evidence_types",
        "claim_status_vocabulary",
        "comparison_conditions",
    ):
        _require(document, key, "question")

    assert document["question_type"] in {"path_connectivity", "activation_energy"}, (
        f"unsupported question_type {document['question_type']!r}"
    )
    refs = document["hypothesis_refs"]
    assert isinstance(refs, list) and refs, "hypothesis_refs must be non-empty"
    assert len(set(refs)) == len(refs), "hypothesis_refs must be unique"
    evidence = document["required_evidence_types"]
    assert isinstance(evidence, list) and evidence, "required_evidence_types required"
    assert set(evidence) <= EVIDENCE_TYPES, (
        f"unknown evidence types: {sorted(set(evidence) - EVIDENCE_TYPES)}"
    )
    assert len(set(evidence)) == len(evidence), "required_evidence_types must be unique"
    assert document["claim_status_vocabulary"] == CLAIM_STATUSES, (
        "claim_status_vocabulary must be exactly the frozen four values"
    )

    conditions = document["comparison_conditions"]
    for key in (
        "method",
        "electronic_state",
        "temperature_k",
        "pressure_pa",
        "standard_state",
        "reference_state",
        "energy_unit",
        "compatibility_requirements",
        "unknown_fields",
    ):
        _require(conditions, key, "comparison_conditions")
    assert isinstance(conditions["method"], str) and conditions["method"], (
        "comparison_conditions.method must be a non-empty string"
    )
    state = conditions["electronic_state"]
    assert isinstance(state["charge"], int) and not isinstance(state["charge"], bool)
    assert (
        isinstance(state["multiplicity"], int)
        and not isinstance(state["multiplicity"], bool)
        and state["multiplicity"] >= 1
    )
    if conditions["standard_state"] not in STANDARD_STATES:
        raise UnknownDeclarationError(
            f"standard_state {conditions['standard_state']!r} is invalid"
        )
    assert conditions["energy_unit"] in ENERGY_UNITS, (
        f"unsupported energy_unit {conditions['energy_unit']!r}"
    )
    requirements = conditions["compatibility_requirements"]
    assert isinstance(requirements, list) and requirements, (
        "compatibility_requirements must be non-empty"
    )
    assert set(requirements) <= COMPATIBILITY_REQUIREMENTS, (
        f"unknown compatibility requirements: {sorted(set(requirements) - COMPATIBILITY_REQUIREMENTS)}"
    )
    assert len(set(requirements)) == len(requirements), (
        "compatibility_requirements must be unique"
    )
    unknown_fields = conditions["unknown_fields"]
    assert isinstance(unknown_fields, list)
    for dotted in unknown_fields:
        value = _resolve_path(conditions, dotted)
        if value is _MISSING:
            raise UnknownDeclarationError(
                f"comparison_conditions.unknown_fields names {dotted!r}, which does not exist"
            )
        if value is not None:
            raise UnknownDeclarationError(
                f"comparison_conditions.unknown_fields names {dotted!r} but its value "
                f"is {value!r}, not unknown"
            )
    for key in ("temperature_k", "pressure_pa", "standard_state", "reference_state"):
        if conditions[key] is None and key not in unknown_fields:
            raise UnknownNotDeclaredError(
                f"comparison_conditions.{key} is null but not declared in unknown_fields"
            )


# ── fixture table ───────────────────────────────────────────────────────────

ACCEPTED_FIXTURES = (
    EXAMPLES / "reaction-da.json",
    EXAMPLES / "question-activation.json",
    REACTION_FIXTURES / "valid_simple_reaction.json",
    REACTION_FIXTURES / "ambiguous_stereo.json",
)
REJECTED_FIXTURES = (
    (REACTION_FIXTURES / "duplicate_map.json", DuplicateMapError),
    (REACTION_FIXTURES / "missing_product.json", MissingProductError),
    (REACTION_FIXTURES / "charge_mismatch.json", ChargeConservationError),
    (
        REACTION_FIXTURES / "net_reaction_claimed_as_elementary.json",
        ElementaryStepClaimWithoutEvidenceError,
    ),
)


def _validate_by_name(path: Path) -> None:
    document = load_json(path)
    if path.name.startswith("question-"):
        validate_question(document)
    else:
        validate_reaction(document)


# ── tests ───────────────────────────────────────────────────────────────────


def test_fixture_table_accepted_and_rejected(capsys: Any) -> None:
    """Happy QA: print the accepted/rejected fixture table and assert the split."""
    rows: list[tuple[str, str, str]] = []
    for path in ACCEPTED_FIXTURES:
        _validate_by_name(path)
        rows.append((path.name, "ACCEPTED", "-"))
    for path, expected in REJECTED_FIXTURES:
        try:
            _validate_by_name(path)
        except expected as exc:
            rows.append((path.name, "REJECTED", f"{type(exc).__name__}: {exc}"))
        else:  # pragma: no cover - defensive
            raise AssertionError(f"{path.name} was accepted but must be rejected")

    lines = ["", "ACP2 A-02b fixture table", f"{'fixture':<44} {'verdict':<10} reason"]
    lines.extend(f"{name:<44} {verdict:<10} {reason}" for name, verdict, reason in rows)
    table = "\n".join(lines)
    with capsys.disabled():
        print(table)
    assert "valid_simple_reaction.json" in table
    assert "REJECTED" in table
    assert "ElementaryStepClaimWithoutEvidenceError" in table


_FIXTURE_REFERENCE_RE = re.compile(
    r"`((?:reaction|question)/[A-Za-z0-9_.-]+\.json)`"
)


def test_rule_fixture_references_are_delivered_or_marked_planned() -> None:
    """Every fixture named by R-01…R-10 must exist or be explicitly planned.

    A frozen rule that points at a non-existent fixture is a broken contract
    reference. Missing fixtures are permitted only when the rules document marks
    the exact filename with ``(planned)`` or ``not yet delivered`` on the same
    line; no missing file is created just to satisfy the reference.
    """
    text = SEMANTIC_RULES.read_text(encoding="utf-8")
    referenced = sorted(set(_FIXTURE_REFERENCE_RE.findall(text)))
    assert referenced, "semantic-rules.md names no reaction/question fixtures"

    # R-06 was renamed by A-02b to match the committed fixture; the old name
    # must be gone so the rule cannot point at a phantom file.
    assert "reaction/net_reaction_claimed_as_elementary.json" in referenced
    assert "reaction/net_as_elementary.json" not in referenced

    unmarked: list[str] = []
    for name in referenced:
        if (CONTRACT_FIXTURES / name).is_file():
            continue
        marked = any(
            name in line and ("(planned)" in line or "not yet delivered" in line)
            for line in text.splitlines()
        )
        if not marked:
            unmarked.append(name)
    assert not unmarked, (
        "rule-referenced fixtures are missing and not marked planned: "
        f"{unmarked}"
    )


def test_valid_simple_reaction_is_accepted() -> None:
    validate_reaction(load_json(REACTION_FIXTURES / "valid_simple_reaction.json"))


def test_reaction_da_example_is_accepted_and_round_trips() -> None:
    path = EXAMPLES / "reaction-da.json"
    raw = path.read_text(encoding="utf-8")
    document = load_json(path)
    validate_reaction(document)
    assert canonical(document) == raw, "example must be canonical JSON on disk"

    reloaded = json.loads(canonical(document))
    validate_reaction(reloaded)
    assert reloaded["atom_mapping"] == document["atom_mapping"]
    for side in ("left", "right"):
        assert [item["charge"] for item in reloaded[side]] == [
            item["charge"] for item in document[side]
        ]
        assert [item["multiplicity"] for item in reloaded[side]] == [
            item["multiplicity"] for item in document[side]
        ]
        assert [item["atom_ids"] for item in reloaded[side]] == [
            item["atom_ids"] for item in document[side]
        ], "atom order must be byte-stable across the round-trip"
    assert canonical(reloaded) == raw
    assert document["reaction_scope"] == "net_reaction"
    for side in ("left", "right"):
        for item in document[side]:
            assert item["stereochemistry"]["status"] == "unknown"
            assert item["stereochemistry"]["centers"] == []
            assert item["stereochemistry"]["double_bonds"] == []


def test_valid_simple_reaction_round_trips_canonically() -> None:
    path = REACTION_FIXTURES / "valid_simple_reaction.json"
    raw = path.read_text(encoding="utf-8")
    document = load_json(path)
    assert canonical(document) == raw
    reloaded = json.loads(canonical(document))
    validate_reaction(reloaded)
    assert reloaded["atom_mapping"] == document["atom_mapping"]


def test_duplicate_map_rejected_with_named_error() -> None:
    with pytest_raises(DuplicateMapError) as info:
        validate_reaction(load_json(REACTION_FIXTURES / "duplicate_map.json"))
    assert "ethane:ethane_c1" in str(info.value)


def test_missing_product_rejected_with_named_error() -> None:
    with pytest_raises(MissingProductError) as info:
        validate_reaction(load_json(REACTION_FIXTURES / "missing_product.json"))
    assert "product" in str(info.value).lower()


def test_charge_mismatch_rejected_with_named_error() -> None:
    with pytest_raises(ChargeConservationError) as info:
        validate_reaction(load_json(REACTION_FIXTURES / "charge_mismatch.json"))
    assert "+0" in str(info.value) and "+1" in str(info.value)


def test_net_reaction_claimed_as_elementary_rejected_with_named_error() -> None:
    with pytest_raises(ElementaryStepClaimWithoutEvidenceError) as info:
        validate_reaction(
            load_json(REACTION_FIXTURES / "net_reaction_claimed_as_elementary.json")
        )
    assert "elementary_step" in str(info.value)


def test_ambiguous_stereo_stays_unknown_and_round_trips() -> None:
    path = REACTION_FIXTURES / "ambiguous_stereo.json"
    raw = path.read_text(encoding="utf-8")
    document = load_json(path)
    validate_reaction(document)
    assert canonical(document) == raw

    stereo = document["right"][0]["stereochemistry"]
    assert stereo["status"] == "unknown"
    assert [center["configuration"] for center in stereo["centers"]] == ["unknown"]
    assert [bond["configuration"] for bond in stereo["double_bonds"]] == ["unknown"]

    reloaded = json.loads(canonical(document))
    validate_reaction(reloaded)
    assert reloaded["right"][0]["stereochemistry"] == stereo


def test_claimed_stereo_without_evidence_rejected_with_named_error() -> None:
    document = copy.deepcopy(load_json(REACTION_FIXTURES / "ambiguous_stereo.json"))
    stereo = document["right"][0]["stereochemistry"]
    stereo["status"] = "defined"
    stereo["centers"][0]["configuration"] = "R"
    with pytest_raises(InventedStereoError) as info:
        validate_reaction(document)
    assert "unknown" in str(info.value).lower()


def test_no_fixture_invents_a_stereo_assignment() -> None:
    for path in (*REACTION_FIXTURES.glob("*.json"), EXAMPLES / "reaction-da.json"):
        document = load_json(path)
        for side in ("left", "right"):
            for item in document.get(side, []):
                stereo = item["stereochemistry"]
                for center in stereo["centers"]:
                    assert center["configuration"] == "unknown", path.name
                for bond in stereo["double_bonds"]:
                    assert bond["configuration"] == "unknown", path.name


def test_removing_product_side_is_rejected() -> None:
    """QA failure: remove the product structure from the valid example."""
    document = copy.deepcopy(load_json(EXAMPLES / "reaction-da.json"))
    document["right"] = []
    with pytest_raises(MissingProductError):
        validate_reaction(document)


def test_flipping_product_charge_is_rejected() -> None:
    """QA failure: flip a product charge in the valid example."""
    document = copy.deepcopy(load_json(EXAMPLES / "reaction-da.json"))
    document["right"][0]["charge"] = 1
    with pytest_raises(ChargeConservationError):
        validate_reaction(document)


def test_element_mismatch_is_rejected_with_named_error() -> None:
    document = copy.deepcopy(load_json(REACTION_FIXTURES / "valid_simple_reaction.json"))
    document["right"][0]["elements"] = document["right"][0]["elements"][:-1]
    document["right"][0]["atom_ids"] = document["right"][0]["atom_ids"][:-1]
    document["right"][0]["formula"] = "C2H5"
    document["atom_mapping"]["left_to_right"].pop("hydrogen:hydrogen_h2")
    document["atom_mapping"]["right_to_left"].pop("ethane:ethane_h6")
    document["atom_mapping"]["unmapped_left"].append("hydrogen:hydrogen_h2")
    with pytest_raises(ElementConservationError):
        validate_reaction(document)


def test_undeclared_null_condition_is_rejected() -> None:
    document = copy.deepcopy(load_json(REACTION_FIXTURES / "valid_simple_reaction.json"))
    document["input_uncertainty"]["unknown_fields"] = []
    with pytest_raises(UnknownNotDeclaredError):
        validate_reaction(document)


def test_question_activation_example_accepted_and_canonical() -> None:
    path = EXAMPLES / "question-activation.json"
    raw = path.read_text(encoding="utf-8")
    document = load_json(path)
    validate_question(document)
    assert canonical(document) == raw
    assert document["question_type"] == "activation_energy"


def test_question_with_forbidden_field_rejected_with_named_error() -> None:
    base = load_json(EXAMPLES / "question-activation.json")
    for field in ("job_status", "role", "cost", "price"):
        document = copy.deepcopy(base)
        document[field] = "forbidden"
        with pytest_raises(QuestionForbiddenFieldError) as info:
            validate_question(document)
        assert field in str(info.value)


def test_question_nested_forbidden_field_rejected() -> None:
    document = copy.deepcopy(load_json(EXAMPLES / "question-activation.json"))
    document["comparison_conditions"]["queue"] = "forbidden"
    with pytest_raises(QuestionForbiddenFieldError) as info:
        validate_question(document)
    assert "queue" in str(info.value)


# ── tiny stdlib pytest-free assertion helper (keeps this module runnable) ───


class pytest_raises:
    """Minimal ``pytest.raises`` equivalent so the checker is import-independent."""

    def __init__(self, expected: type[BaseException]) -> None:
        self.expected = expected
        self.value: BaseException | None = None

    def __enter__(self) -> pytest_raises:
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        if exc_type is None:
            raise AssertionError(f"expected {self.expected.__name__}, nothing was raised")
        if not issubclass(exc_type, self.expected):
            return False
        self.value = exc
        return True
