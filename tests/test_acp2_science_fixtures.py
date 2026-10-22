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

import hashlib
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

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
