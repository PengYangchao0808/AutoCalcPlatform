"""Contract checks for the ACP2 singlepoint science fixture (A-09b).

The frozen fixture is ``tests/fixtures/acp2/science/singlepoint``.  This module
validates it with the authoritative CCCP contracts, never with a second
scientific checker:

* digests are recomputed from the bytes and compared with
  ``fixture-manifest.json`` (plus a pinned input digest so the manifest and the
  file cannot drift together);
* the request is parsed by ``TaskRequest.from_dict`` and validated by
  ``validate_request`` and the side-effect-free capability preflight;
* the four negative cases are rejected for their named reason by
  ``validate_request``, the capability preflight, the electronic-state parity
  validator or the method registry;
* no reference energy is invented before a real run.

Two negatives come from the frozen fixture files (``missing_structure.json``
and ``invalid_electrons.json``); two are constructed in test from the valid
request and are labelled as such.  No QC executable is launched and no
trajectory/energy is fabricated by this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cccp.analysis.research.reaction import (
    DuplicateMapError,
    InventedStereoError,
    MissingProductError,
    Reaction,
)
from cccp.calculation._common import electron_count, resolve_multiplicity
from cccp.calculation.capabilities import (
    PreflightCheck,
    PreflightOutcome,
    PreflightResult,
    bind_task_kind,
    preflight_binding,
)
from cccp.calculation.contracts import (
    electron_parity_ok,
    validate_electronic_state_spec,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import TaskRequest, validate_request
from cccp.io.input_handler import MolecularInputHandler
from cccp.qc.keyword_registry import (
    KeywordValueError,
    method_family,
    resolve_implementation,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO / "tests" / "fixtures" / "acp2" / "science" / "singlepoint"

MANIFEST_PATH = FIXTURE_DIR / "fixture-manifest.json"
REQUEST_PATH = FIXTURE_DIR / "request.json"
EXPECTED_PATH = FIXTURE_DIR / "expected-science.json"
INPUT_PATH = FIXTURE_DIR / "input.xyz"
INVALID_ELECTRONS_PATH = FIXTURE_DIR / "invalid_electrons.json"
MISSING_STRUCTURE_PATH = FIXTURE_DIR / "missing_structure.json"

#: Pinned independently of the manifest so a simultaneous manifest+file edit
#: cannot move the frozen input silently.
EXPECTED_INPUT_SHA256 = "8745d09f651973bc3acbf2d638baf1d46e28b1f0892a1b8c30becbd6de04ed1a"

#: Provenance of each negative case.  ``constructed`` cases are built in test
#: from the valid request; ``frozen_fixture`` cases are committed negative
#: fixtures.  Neither is a real calculation.
NEGATIVE_CASE_PROVENANCE = {
    "invalid_electron_count": "constructed",
    "missing_structure": "frozen_fixture",
    "wrong_method": "constructed",
    "incompatible_multiplicity": "frozen_fixture",
}


def _load_json(path: Path) -> dict[str, Any]:
    """Parse a fixture JSON document."""
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
        return payload


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight(request: TaskRequest) -> PreflightResult:
    """Run the side-effect-free singlepoint capability preflight."""
    return preflight_binding(bind_task_kind("singlepoint", request))


def _failed_checks(preflight: PreflightResult) -> dict[str, PreflightCheck]:
    """Index the failed preflight checks by id."""
    return {check.id: check for check in preflight.failures()}


def _valid_request_payload() -> dict[str, Any]:
    """Return a fresh copy of the frozen valid request payload."""
    return _load_json(REQUEST_PATH)


def _invalid_electron_count_case() -> tuple[str, str]:
    """Reject a request whose charge leaves zero electrons (constructed)."""
    payload = _valid_request_payload()
    payload["charge"] = 10
    request = TaskRequest.from_dict(payload)
    preflight = _preflight(request)
    checks = _failed_checks(preflight)
    assert preflight.outcome is PreflightOutcome.NOT_READY
    assert "charge_multiplicity_consistent" in checks
    check = checks["charge_multiplicity_consistent"]
    assert check.satisfied is False
    actual = check.actual
    assert isinstance(actual, dict)
    assert actual["n_electrons"] == 0
    detail = f"{check.id}: {check.reason} (charge=10 -> n_electrons=0)"
    return "invalid_electron_count", detail


def _missing_structure_case() -> tuple[str, str]:
    """Reject the frozen no-structure fixture in the request envelope."""
    payload = _load_json(MISSING_STRUCTURE_PATH)
    with pytest.raises(TaskInputError) as excinfo:
        TaskRequest.from_dict(payload)
    message = str(excinfo.value)
    assert "requires a structure input" in message
    return "missing_structure", f"validate_request: {message}"


def _wrong_method_case() -> tuple[str, str]:
    """Reject an unregistered method in the method registry (constructed).

    ``TaskRequest.from_dict`` accepts the payload shape, so the rejection is
    scientific rather than structural: ``method_family`` returns ``unknown``
    and ``resolve_implementation`` refuses to invent a family/implementation.
    """
    payload = _valid_request_payload()
    payload["level"] = {"method": "not-a-registered-method", "basis": "def2-SVP"}
    request = TaskRequest.from_dict(payload)
    assert request.level.method == "not-a-registered-method"
    assert method_family(request.level.method) == "unknown"
    with pytest.raises(KeywordValueError) as excinfo:
        resolve_implementation(request.level.method, engine="orca")
    message = str(excinfo.value)
    assert "not in the registry's method table" in message
    assert resolve_implementation("wB97X-D4", engine="orca")
    return "wrong_method", f"method registry: {message}"


def _incompatible_multiplicity_case() -> tuple[str, str]:
    """Reject the frozen 10-electron doublet before any backend call."""
    payload = _load_json(INVALID_ELECTRONS_PATH)
    request = TaskRequest.from_dict(payload)
    assert request.structure is not None
    symbols = tuple(request.structure.symbols or ())
    n_electrons = electron_count(symbols, request.charge)
    assert n_electrons == 10
    assert request.multiplicity == 2
    assert not electron_parity_ok(n_electrons, request.multiplicity)

    assert request.electronic_state is not None
    validation = validate_electronic_state_spec(
        request.electronic_state,
        n_electrons=n_electrons,
        n_atoms=len(symbols),
    )
    assert any("parity mismatch" in error for error in validation.errors)

    with pytest.raises(ValueError, match="incompatible with the electron count"):
        resolve_multiplicity(
            request.electronic_state,
            symbols,
            request.charge,
            request.multiplicity,
        )

    preflight = _preflight(request)
    checks = _failed_checks(preflight)
    assert preflight.outcome is PreflightOutcome.NOT_READY
    assert "charge_multiplicity_consistent" in checks
    assert "electronic_state_applicable" in checks
    detail = "electron parity: 10 electrons are incompatible with multiplicity 2 (parity mismatch)"
    return "incompatible_multiplicity", detail


NEGATIVE_CASES = (
    pytest.param(
        _invalid_electron_count_case,
        "invalid_electron_count",
        id="invalid-electron-count",
    ),
    pytest.param(
        _missing_structure_case,
        "missing_structure",
        id="missing-structure",
    ),
    pytest.param(
        _wrong_method_case,
        "wrong_method",
        id="wrong-method",
    ),
    pytest.param(
        _incompatible_multiplicity_case,
        "incompatible_multiplicity",
        id="incompatible-multiplicity",
    ),
)


def test_singlepoint_fixture_digests_match_manifest() -> None:
    """Every frozen file digest matches the manifest and the pinned input."""
    manifest = _load_json(MANIFEST_PATH)
    digests = manifest["file_digests"]
    assert digests["input.xyz"] == manifest["input"]["sha256"] == EXPECTED_INPUT_SHA256
    for name, expected in digests.items():
        actual = _sha256_file(FIXTURE_DIR / name)
        assert actual == expected, f"{name} digest drifted: {actual} != {expected}"


def test_singlepoint_fixture_required_scientific_fields() -> None:
    """Charge, multiplicity, method, basis, atom order, units and tolerances."""
    manifest = _load_json(MANIFEST_PATH)
    request = TaskRequest.from_dict(_load_json(REQUEST_PATH))
    expected_science = _load_json(EXPECTED_PATH)

    inputs = manifest["input"]
    parameters = manifest["scientific_parameters"]
    tolerances = manifest["tolerances"]

    assert inputs["atom_order"] == ["O", "H", "H"]
    assert inputs["charge"] == request.charge == parameters["charge"] == 0
    assert inputs["multiplicity"] == request.multiplicity == parameters["multiplicity"] == 1
    assert inputs["electron_count"] == 10
    assert inputs["coordinates_unit"] == "angstrom"
    assert inputs["closed_shell"] is True

    assert request.level.method == parameters["method"] == "wB97X-D4"
    assert request.level.basis == parameters["basis"] == "def2-SVP"
    assert parameters["energy_unit"] == "hartree"
    assert parameters["task"] == request.task.value == "singlepoint"

    assert request.structure is not None
    assert list(request.structure.symbols or ()) == inputs["atom_order"]
    assert isinstance(tolerances["coordinate_angstrom_abs"], float)
    assert isinstance(tolerances["reparse_energy_hartree_abs"], float)
    assert tolerances["reference_energy_hartree_abs"] is None
    for key, value in expected_science["tolerances"].items():
        assert tolerances[key] == value

    check_ids = {check["check_id"] for check in expected_science["checks"]}
    assert check_ids == {
        "parse_correctness",
        "normal_termination",
        "energy_presence",
        "unit_correctness",
    }
    assert expected_science["result_expectations"]["energy_unit"] == "hartree"
    assert expected_science["result_expectations"]["task"] == "singlepoint"


def test_singlepoint_fixture_input_parses_to_declared_order() -> None:
    """The frozen XYZ parses to the declared atom order and inline geometry."""
    manifest = _load_json(MANIFEST_PATH)
    request = TaskRequest.from_dict(_load_json(REQUEST_PATH))
    assert request.structure is not None
    parsed = MolecularInputHandler.from_source(
        INPUT_PATH,
        charge=request.charge,
        multiplicity=request.multiplicity,
    )
    assert list(parsed.symbols) == manifest["input"]["atom_order"]
    assert int(parsed.charge) == manifest["input"]["charge"]
    assert int(parsed.multiplicity) == manifest["input"]["multiplicity"]
    tolerance = manifest["tolerances"]["coordinate_angstrom_abs"]
    for parsed_row, request_row in zip(
        parsed.coordinates, request.structure.coordinates or (), strict=True
    ):
        for parsed_value, request_value in zip(parsed_row, request_row, strict=True):
            assert math.isclose(
                float(parsed_value), float(request_value), rel_tol=0.0, abs_tol=tolerance
            )


def test_singlepoint_valid_request_passes_authoritative_validation() -> None:
    """The frozen request is ready in the existing scientific preflight."""
    request = TaskRequest.from_dict(_load_json(REQUEST_PATH))
    validate_request(request)
    preflight = _preflight(request)
    assert preflight.outcome is PreflightOutcome.READY
    assert all(check.satisfied is not False for check in preflight.checks)
    check_ids = {check.id for check in preflight.checks}
    assert {
        "structure_supplied",
        "structure_geometry_finite_angstrom",
        "charge_multiplicity_consistent",
        "method_or_basis",
    } <= check_ids


@pytest.mark.parametrize("checker,reason_id", NEGATIVE_CASES)
def test_singlepoint_negative_cases_fail_for_named_reason(
    checker: Callable[[], tuple[str, str]], reason_id: str
) -> None:
    """Each negative case is rejected for its own named reason."""
    actual_reason, detail = checker()
    assert actual_reason == reason_id
    assert NEGATIVE_CASE_PROVENANCE[reason_id] in {"constructed", "frozen_fixture"}
    print(f"{reason_id} ({NEGATIVE_CASE_PROVENANCE[reason_id]}): {detail}")


def _has_numeric_reference_energy(document: Any) -> bool:
    """Return True when a numeric reference energy is present anywhere."""
    if isinstance(document, dict):
        for key, value in document.items():
            if key in {"reference_energy", "reference_energy_hartree_abs"}:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return True
            if _has_numeric_reference_energy(value):
                return True
    elif isinstance(document, list):
        return any(_has_numeric_reference_energy(item) for item in document)
    return False


def test_singlepoint_has_no_invented_reference_energy() -> None:
    """reference_energy stays null and the missing measurement is named."""
    manifest = _load_json(MANIFEST_PATH)
    expected_science = _load_json(EXPECTED_PATH)

    assert expected_science["reference_energy"] is None
    assert manifest["reference_energy"] is None
    assert manifest["energy_state"] == "not_measured"
    assert expected_science["tolerances"]["reference_energy_hartree_abs"] is None
    assert manifest["tolerances"]["reference_comparison_available"] is False
    assert not _has_numeric_reference_energy(manifest)
    assert not _has_numeric_reference_energy(expected_science)

    gap = expected_science["reference_energy_gap"]
    assert gap["state"] == "NOT_MEASURED"
    assert gap["reason"].strip()
    assert gap["run_key"] is None
    assert gap["run_key_gap"].strip()
    assert gap["receipt_protocol_gap"].strip()
    assert gap["unblock_requirements"]


DA_FIXTURE_DIR = REPO / "tests" / "fixtures" / "acp2" / "science" / "diels_alder" / "v1"
DA_REACTION_PATH = DA_FIXTURE_DIR / "reaction.json"
DA_QUESTION_PATH = DA_FIXTURE_DIR / "question.json"
DA_MANIFEST_PATH = DA_FIXTURE_DIR / "fixture-manifest.json"
DA_ACCEPTANCE_PATH = DA_FIXTURE_DIR / "acceptance.json"
DA_REACTANTS_PATH = DA_FIXTURE_DIR / "reactants.xyz"
DA_PRODUCT_PATH = DA_FIXTURE_DIR / "product.xyz"
DA_FIXTURE_ID = "diels_alder-butadiene-ethene-wb97xd4-def2tzvp"

_DA_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")


def _da_reaction_payload() -> dict[str, Any]:
    """Return a mutable deep copy of the frozen DA reaction payload."""
    return copy.deepcopy(_load_json(DA_REACTION_PATH))


def _da_formula_counts(formula: str) -> Counter[str]:
    """Count elements in a Hill-order fixture formula."""
    counts: Counter[str] = Counter()
    position = 0
    for match in _DA_FORMULA_TOKEN.finditer(formula):
        assert match.start() == position, f"malformed DA formula {formula!r}"
        symbol, raw_count = match.group(1), match.group(2)
        counts[symbol] += int(raw_count) if raw_count else 1
        position = match.end()
    assert position == len(formula) and counts, f"malformed DA formula {formula!r}"
    return counts


def _da_xyz_symbols(path: Path) -> list[str]:
    """Return the element column of a frozen DA XYZ file in file order."""
    lines = path.read_text(encoding="utf-8").splitlines()
    atom_count = int(lines[0].strip())
    atom_lines = [line for line in lines[2:] if line.strip()]
    assert len(atom_lines) == atom_count, (
        f"{path.name}: declared {atom_count} atoms but found {len(atom_lines)}"
    )
    symbols = [line.split()[0] for line in atom_lines]
    assert all(len(line.split()) == 4 for line in atom_lines), f"{path.name}: malformed atom line"
    return symbols


def _walk_da(payload: Any, path: str = "$") -> Any:
    """Yield every ``(path, node)`` pair in a fixture document."""
    yield path, payload
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from _walk_da(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from _walk_da(value, f"{path}[{index}]")


def _assert_no_endo_exo_request(payload: dict[str, Any], name: str) -> None:
    """Only the frozen refusal records may mention endo/exo; requested stays false."""
    for path, node in _walk_da(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            lowered = key.lower()
            if "endo" not in lowered and "exo" not in lowered:
                continue
            where = f"{name}:{path}.{key}"
            if lowered == "endo_exo_selectivity_supported":
                assert value is False, f"{where} must be false"
            elif lowered == "endo_exo_selectivity":
                if isinstance(value, dict):
                    assert value.get("requested") is False, f"{where}.requested must be false"
                    assert value.get("supported_by_fixture") is False, (
                        f"{where}.supported_by_fixture must be false"
                    )
                    assert value.get("status") == "not_evaluated", (
                        f"{where}.status must stay not_evaluated"
                    )
                else:
                    assert value is False, f"{where} must be the explicit false refusal"
            else:
                raise AssertionError(
                    f"{where} looks like an endo/exo request; the DA fixture forbids it"
                )


def test_diels_alder_fixture_mapping_is_unique_one_to_one_and_exact() -> None:
    """The mapping covers every atom once and both directions are exact inverses."""
    reaction = Reaction.from_dict(_load_json(DA_REACTION_PATH))
    mapping = reaction.atom_mapping

    left_addresses = {
        f"{structure.structure_id}:{atom_id}"
        for structure in reaction.left
        for atom_id in structure.atom_ids
    }
    right_addresses = {
        f"{structure.structure_id}:{atom_id}"
        for structure in reaction.right
        for atom_id in structure.atom_ids
    }

    assert len(mapping.left_to_right) == 16
    assert set(mapping.left_to_right) == left_addresses
    assert len(set(mapping.left_to_right.values())) == len(mapping.left_to_right)
    assert mapping.right_to_left == {right: left for left, right in mapping.left_to_right.items()}
    assert set(mapping.right_to_left) == right_addresses
    assert not mapping.unmapped_left
    assert not mapping.unmapped_right
    print(
        "1. exact mapping: 16 unique one-to-one pairs, exact inverse, "
        "no unmapped atoms on either side"
    )


def test_diels_alder_fixture_conserves_elements_and_charge() -> None:
    """Elements, side formulas and net charge balance across the net reaction."""
    reaction = Reaction.from_dict(_load_json(DA_REACTION_PATH))

    left_elements: Counter[str] = Counter()
    left_charge = 0
    for structure in reaction.left:
        assert len(structure.atom_ids) == len(structure.elements)
        assert _da_formula_counts(structure.formula) == Counter(structure.elements)
        left_elements.update(structure.elements)
        left_charge += structure.charge

    right_elements: Counter[str] = Counter()
    right_charge = 0
    for structure in reaction.right:
        assert len(structure.atom_ids) == len(structure.elements)
        assert _da_formula_counts(structure.formula) == Counter(structure.elements)
        right_elements.update(structure.elements)
        right_charge += structure.charge

    assert left_elements == right_elements == Counter({"C": 6, "H": 10})
    assert left_charge == right_charge == 0
    assert sum(len(structure.atom_ids) for structure in reaction.left) == 16
    print("2. conservation: C6H10 and net charge 0 are identical on both sides")


def test_diels_alder_rejects_a_duplicate_mapping_claim() -> None:
    """A right-side atom claimed twice is rejected before a mapping exists."""
    payload = _da_reaction_payload()
    payload["atom_mapping"]["left_to_right"]["ethene:et_c1"] = "cyclohexene:cy_c1"

    with pytest.raises(DuplicateMapError, match="more than once"):
        Reaction.from_dict(payload)
    print("3. duplicate-map rejection: a second claim on cyclohexene:cy_c1 is refused")


def test_diels_alder_rejects_a_missing_product_side() -> None:
    """An empty right side is an incomplete transformation, not a reaction."""
    payload = _da_reaction_payload()
    payload["right"] = []

    with pytest.raises(MissingProductError, match="at least one product"):
        Reaction.from_dict(payload)
    print("4. missing-product rejection: an empty right side is refused")


def test_diels_alder_rejects_an_invented_stereo_assignment() -> None:
    """A declared-unknown stereo fact cannot be silently promoted to R/S."""
    payload = _da_reaction_payload()
    payload["left"][0]["stereochemistry"] = {
        "status": "unknown",
        "centers": [{"atom_id": "bd_c1", "configuration": "R"}],
        "double_bonds": [],
    }

    with pytest.raises(InventedStereoError, match="cannot carry"):
        Reaction.from_dict(payload)

    reaction = Reaction.from_dict(_load_json(DA_REACTION_PATH))
    assert all(structure.stereochemistry.status == "unknown" for structure in reaction.left)
    assert all(structure.stereochemistry.status == "unknown" for structure in reaction.right)
    assert all(not structure.stereochemistry.centers for structure in reaction.left)
    assert all(not structure.stereochemistry.double_bonds for structure in reaction.left)
    assert reaction.to_dict() == _load_json(DA_REACTION_PATH)
    print("5. ambiguous-stereo rejection: an R assignment under status=unknown is refused")


def test_diels_alder_fixture_declares_xyz_atom_order() -> None:
    """Both XYZ files carry exactly the declared atom_ids/elements order."""
    reaction = Reaction.from_dict(_load_json(DA_REACTION_PATH))

    declared_left = [symbol for structure in reaction.left for symbol in structure.elements]
    declared_right = [symbol for structure in reaction.right for symbol in structure.elements]
    assert [atom_id for structure in reaction.left for atom_id in structure.atom_ids] == [
        "bd_c1",
        "bd_c2",
        "bd_c3",
        "bd_c4",
        "bd_h1",
        "bd_h2",
        "bd_h3",
        "bd_h4",
        "bd_h5",
        "bd_h6",
        "et_c1",
        "et_c2",
        "et_h1",
        "et_h2",
        "et_h3",
        "et_h4",
    ]
    assert [atom_id for structure in reaction.right for atom_id in structure.atom_ids] == [
        "cy_c1",
        "cy_c2",
        "cy_c3",
        "cy_c4",
        "cy_c5",
        "cy_c6",
        "cy_h1",
        "cy_h2",
        "cy_h3",
        "cy_h4",
        "cy_h5",
        "cy_h6",
        "cy_h7",
        "cy_h8",
        "cy_h9",
        "cy_h10",
    ]
    assert _da_xyz_symbols(DA_REACTANTS_PATH) == declared_left
    assert _da_xyz_symbols(DA_PRODUCT_PATH) == declared_right


def test_diels_alder_fixture_requests_no_endo_exo_selectivity() -> None:
    """No question, field or claim in the DA fixture requests endo/exo."""
    question_set = _load_json(DA_QUESTION_PATH)
    manifest = _load_json(DA_MANIFEST_PATH)
    acceptance = _load_json(DA_ACCEPTANCE_PATH)

    assert question_set["fixture_id"] == DA_FIXTURE_ID
    assert {question["question_type"] for question in question_set["questions"]} == {
        "path_connectivity",
        "activation_energy",
    }
    refusal = next(
        item for item in question_set["out_of_scope"] if item["question"] == "endo_exo_selectivity"
    )
    assert refusal["requested"] is False
    assert refusal["supported_by_fixture"] is False

    assert manifest["scope"]["endo_exo_selectivity_supported"] is False
    assert manifest["scope"]["out_of_scope"][0] == "endo/exo selectivity"

    claim = acceptance["claim_status"]["endo_exo_selectivity"]
    assert claim["requested"] is False
    assert claim["supported_by_fixture"] is False
    assert claim["status"] == "not_evaluated"

    for name, payload in (
        ("reaction.json", _load_json(DA_REACTION_PATH)),
        ("question.json", question_set),
        ("fixture-manifest.json", manifest),
        ("acceptance.json", acceptance),
    ):
        _assert_no_endo_exo_request(payload, name)
