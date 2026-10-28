"""A-19a: electronic vs free-energy activation-barrier comparability.

Every fixture here is **constructed**.  No energy was computed, no QC
subprocess ran, and no experimental or computed barrier is claimed.  The
method/basis/electronic-state identity mirrors the A-17 audited constructed
snapshot, but the energies are placeholders chosen only to make the
identity rules testable.

The tests pin the rules the plan requires:

* electronic and free-energy barriers are separate claims and are never
  subtracted from mixed kinds;
* method, electronic state, temperature, standard state, reference state and
  reactant stoichiometry must agree before a barrier may be computed;
* incomplete thermochemistry reports only the electronic barrier and never
  invents a free-energy barrier;
* a standard-state token the existing thermochemistry contract cannot
  standardize (for example the frozen DA fixture's ``1_bar_ideal_gas``) keeps
  the electronic barrier and makes no free-energy claim;
* a missing identity is ``inconclusive`` or ``not_evaluated``, never a
  fabricated number.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from cccp.analysis.research import (
    ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID,
    CLAIM_STATUSES,
    REACTANT_STOICHIOMETRY_VALIDATOR_ID,
    VALIDATOR_VERSION,
    ActivationEnergyValidation,
    BarrierEnergyRecord,
    EnergyIdentity,
    energy_identity_from_thermochemistry,
    validate_activation_energy_comparability,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "energy"
CASES_PATH = FIXTURES / "barrier-cases.json"
MANIFEST_PATH = FIXTURES / "fixture-manifest.json"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
        return payload


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cases() -> dict[str, Any]:
    return _load_json(CASES_PATH)["cases"]


def _validate(case: dict[str, Any]) -> ActivationEnergyValidation:
    return validate_activation_energy_comparability(
        case["reactants"],
        case["transition_state"],
        case["product"],
        required_reactants=case["required_reactants"],
        reaction_id=case["reaction_id"],
    )


def _missing_ids(validation: ActivationEnergyValidation, claim: str) -> set[str]:
    target = getattr(validation, claim)
    return {check.check_id for check in target.missing_checks}


def _assert_no_barrier_value(claim: Any) -> None:
    assert claim.details["barrier_value"] is None
    assert claim.details["subtraction_performed"] is False


def test_fixture_is_constructed_digest_frozen_and_mirrors_a17_identity() -> None:
    manifest = _load_json(MANIFEST_PATH)
    document = _load_json(CASES_PATH)
    assert manifest["schema"] == "acp2.science.fixture-manifest"
    assert manifest["schema_version"] == 1
    assert manifest["constructed"] is True
    assert manifest["real_run"] is False
    assert document["schema"] == "acp2.science.energy-comparability-cases"
    assert document["constructed"] is True
    assert document["real_run"] is False
    assert document["energies_are_placeholders"] is True
    assert set(manifest["file_digests"]) == {"barrier-cases.json"}
    for relative, expected in manifest["file_digests"].items():
        assert _sha256_file(FIXTURES / relative) == expected, relative
    assert manifest["thresholds"]["validator_version"] == VALIDATOR_VERSION
    assert set(manifest["thresholds"]["claim_statuses"]) == set(CLAIM_STATUSES)

    source = document["source_identity"]
    assert source["a17_snapshot_constructed"] is True
    assert source["b20a_exec_artifacts_present"] is False
    a17 = _load_json(REPO / source["a17_snapshot"])
    identity = a17["snapshot"]["scientific_identity"]
    assert identity["method"] == source["method"]
    assert identity["basis"] == source["basis"]
    assert identity["electronic_state"] == source["electronic_state"]


def test_valid_electronic_barrier_is_supported_without_a_free_energy_claim() -> None:
    validation = _validate(_cases()["valid_electronic"])
    assert validation.reactant_stoichiometry.status == "supported"
    assert validation.electronic_barrier.status == "supported"
    assert validation.electronic_barrier.validator_id == (
        ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID
    )
    assert validation.electronic_barrier.details["barrier_kind"] == "electronic"
    assert validation.electronic_barrier.details["barrier_value"] == pytest.approx(0.1)
    assert validation.electronic_barrier.details["barrier_unit"] == "hartree"
    assert validation.electronic_barrier.details["subtraction_performed"] is True

    assert validation.free_energy_barrier.status == "not_evaluated"
    _assert_no_barrier_value(validation.free_energy_barrier)
    assert validation.free_energy_claimed is False
    assert validation.details["free_energy_claimed"] is False
    assert _missing_ids(validation, "free_energy_barrier") == {"frequency_not_computed"}
    assert "no thermochemistry" in validation.free_energy_barrier.missing_checks[0].reason


def test_valid_free_energy_barrier_is_a_separate_supported_claim() -> None:
    validation = _validate(_cases()["valid_free_energy"])
    assert validation.electronic_barrier.status == "supported"
    assert validation.free_energy_barrier.status == "supported"
    assert validation.free_energy_claimed is True
    assert validation.status == "supported"
    assert validation.free_energy_barrier.details["barrier_kind"] == "gibbs_free"
    assert validation.free_energy_barrier.details["barrier_value"] == pytest.approx(0.1)
    assert validation.free_energy_barrier.details["barrier_expression"].startswith("G(TS)")
    assert validation.electronic_barrier.details["barrier_expression"].startswith("E(TS)")


def test_mixed_electronic_and_free_energies_are_refused() -> None:
    validation = _validate(_cases()["mixed_kind"])
    assert validation.details["mixed_energy_kinds"] is True
    for claim in (validation.electronic_barrier, validation.free_energy_barrier):
        assert claim.status == "refuted"
        _assert_no_barrier_value(claim)
    assert "never subtracted together" in " ".join(validation.electronic_barrier.notes)


def test_different_standard_states_are_refused() -> None:
    validation = _validate(_cases()["different_standard_state"])
    assert validation.electronic_barrier.status == "supported"
    assert validation.free_energy_barrier.status == "refuted"
    assert "standard_state" in validation.free_energy_barrier.details["differing_fields"]
    _assert_no_barrier_value(validation.free_energy_barrier)
    assert validation.free_energy_claimed is False


def test_different_methods_are_refused_for_both_barriers() -> None:
    validation = _validate(_cases()["different_method"])
    assert validation.electronic_barrier.status == "refuted"
    assert validation.free_energy_barrier.status == "refuted"
    assert "method" in validation.electronic_barrier.details["differing_fields"]
    assert "method" in validation.free_energy_barrier.details["differing_fields"]


@pytest.mark.parametrize(
    ("case_name", "field", "electronic_status", "free_status"),
    [
        ("different_reference_state", "reference_state", "refuted", "refuted"),
        (
            "different_electronic_state",
            "electronic_state.state_id",
            "refuted",
            "refuted",
        ),
        ("different_temperature", "temperature_k", "supported", "refuted"),
    ],
)
def test_identity_mismatches_are_refused(
    case_name: str,
    field: str,
    electronic_status: str,
    free_status: str,
) -> None:
    validation = _validate(_cases()[case_name])
    assert validation.electronic_barrier.status == electronic_status
    assert validation.free_energy_barrier.status == free_status
    if free_status == "refuted":
        assert field in validation.free_energy_barrier.details["differing_fields"]


def test_missing_reactant_component_is_refused() -> None:
    validation = _validate(_cases()["missing_reactant"])
    assert validation.reactant_stoichiometry.status == "refuted"
    assert validation.reactant_stoichiometry.validator_id == (REACTANT_STOICHIOMETRY_VALIDATOR_ID)
    assert validation.reactant_stoichiometry.details["missing_components"] == ["ethene"]
    assert validation.electronic_barrier.status == "refuted"
    assert validation.free_energy_barrier.status == "refuted"
    for claim in (validation.electronic_barrier, validation.free_energy_barrier):
        _assert_no_barrier_value(claim)


def test_wrong_reactant_coefficient_is_refused() -> None:
    validation = _validate(_cases()["wrong_stoichiometry"])
    assert validation.reactant_stoichiometry.status == "refuted"
    assert validation.reactant_stoichiometry.details["coefficient_mismatches"] == {
        "ethene": {"required": 1, "supplied": 2}
    }
    assert validation.electronic_barrier.status == "refuted"


def test_missing_thermochemistry_reports_only_the_electronic_barrier() -> None:
    validation = _validate(_cases()["missing_thermochemistry"])
    assert validation.electronic_barrier.status == "supported"
    assert validation.free_energy_barrier.status == "not_evaluated"
    _assert_no_barrier_value(validation.free_energy_barrier)
    assert validation.details["free_energy_claimed"] is False
    assert validation.details["free_energy_barrier_value"] is None
    assert validation.free_energy_barrier.details["thermochemistry_complete"] is False
    assert _missing_ids(validation, "free_energy_barrier") == {"frequency_not_computed"}


def test_partial_thermochemistry_makes_no_free_energy_claim() -> None:
    validation = _validate(_cases()["partial_thermochemistry"])
    assert validation.electronic_barrier.status == "supported"
    assert validation.free_energy_barrier.status == "inconclusive"
    _assert_no_barrier_value(validation.free_energy_barrier)
    assert validation.free_energy_claimed is False
    assert _missing_ids(validation, "free_energy_barrier") == {"frequency_not_computed"}


def test_unstandardized_standard_state_reports_only_the_electronic_barrier() -> None:
    case = _cases()["unsupported_standard_state"]
    validation = _validate(case)
    assert case["expected"]["declared_standard_state"] == "1_bar_ideal_gas"
    assert validation.electronic_barrier.status == "supported"
    assert validation.free_energy_barrier.status == "not_evaluated"
    _assert_no_barrier_value(validation.free_energy_barrier)
    assert validation.free_energy_claimed is False
    assert _missing_ids(validation, "free_energy_barrier") == {"standard_state_unspecified"}
    rows = validation.free_energy_barrier.details["standard_state_lexicon"]
    assert rows
    assert all(row["declared"] == "1_bar_ideal_gas" for row in rows)
    assert all(row["normalization_is_lossless"] is False for row in rows)
    assert "inventing a token" in validation.free_energy_barrier.missing_checks[0].reason


def test_missing_identity_is_inconclusive_or_not_evaluated() -> None:
    missing_component = _validate(_cases()["missing_identity"])
    assert missing_component.electronic_barrier.status in {
        "inconclusive",
        "not_evaluated",
    }
    assert missing_component.free_energy_barrier.status in {
        "inconclusive",
        "not_evaluated",
    }
    _assert_no_barrier_value(missing_component.electronic_barrier)
    _assert_no_barrier_value(missing_component.free_energy_barrier)

    missing_field = _validate(_cases()["missing_identity_field"])
    assert missing_field.electronic_barrier.status == "inconclusive"
    assert "method_identity_missing" in _missing_ids(missing_field, "electronic_barrier")
    _assert_no_barrier_value(missing_field.electronic_barrier)


def test_all_constructed_cases_match_their_frozen_expectation() -> None:
    for name, case in _cases().items():
        expected = case["expected"]
        validation = _validate(case)
        assert validation.reactant_stoichiometry.status == expected["stoichiometry"], name
        assert validation.electronic_barrier.status == expected["electronic_barrier"], name
        assert validation.free_energy_barrier.status == expected["free_energy_barrier"], name
        for claim, key in (
            (validation.electronic_barrier, "electronic_barrier_value"),
            (validation.free_energy_barrier, "free_energy_barrier_value"),
        ):
            value = claim.details["barrier_value"]
            if expected[key] is None:
                assert value is None, (name, key, value)
            else:
                assert value == pytest.approx(expected[key]), (name, key, value)
        assert validation.details["free_energy_claimed"] is expected["free_energy_claimed"], name


def test_report_is_json_safe_and_scope_is_explicit() -> None:
    validation = _validate(_cases()["valid_free_energy"])
    report = validation.to_dict()
    assert json.loads(json.dumps(report)) == report
    assert report["status"] == "supported"
    assert report["validator_version"] == VALIDATOR_VERSION
    assert len(report["claims"]) == 3
    for claim in report["claims"]:
        assert claim["validator"]["validator_id"].startswith("check.")
        assert claim["validator"]["kind"] == "deterministic"
    assert "no claim about other paths" in report["details"]["claim_scope"]
    assert "component presence and coefficients" in report["details"]["stoichiometry_scope"]


def test_thermochemistry_adapter_consumes_the_existing_payload_shape() -> None:
    payload = {
        "gibbs_hartree": -234.05,
        "standard_state": "1atm",
        "temperature_k": 298.15,
    }
    identity = energy_identity_from_thermochemistry(
        payload,
        method="wB97X-D4",
        electronic_state={"state_id": "state.singlet.s0", "target_multiplicity": 1},
        charge=0,
        multiplicity=1,
        reference_state="isolated_molecule",
        pressure_pa=101325.0,
    )
    assert identity is not None
    assert identity.energy_kind == "gibbs_free"
    assert identity.energy_hartree == pytest.approx(-234.05)
    assert identity.standard_state == "1atm"
    assert identity.temperature_k == pytest.approx(298.15)
    assert identity.method == "wB97X-D4"

    ensemble = energy_identity_from_thermochemistry(
        {"total_gibbs_hartree": -234.06, "temperature_k": 298.15},
        method="wB97X-D4",
        electronic_state={"state_id": "state.singlet.s0"},
    )
    assert ensemble is not None
    assert ensemble.energy_hartree == pytest.approx(-234.06)
    assert ensemble.standard_state is None

    assert (
        energy_identity_from_thermochemistry(
            {"standard_state": "1atm"},
            method="wB97X-D4",
            electronic_state={"state_id": "state.singlet.s0"},
        )
        is None
    )
    assert (
        energy_identity_from_thermochemistry(
            None,
            method="wB97X-D4",
            electronic_state={"state_id": "state.singlet.s0"},
        )
        is None
    )


def test_barrier_record_round_trips_and_rejects_bad_records() -> None:
    record = BarrierEnergyRecord.from_dict(_cases()["valid_free_energy"]["reactants"][0])
    assert record.to_dict()["species_id"] == "butadiene"
    assert set(record.declared_kinds()) == {"electronic", "gibbs_free"}
    assert json.loads(json.dumps(record.to_dict())) == record.to_dict()
    assert record.identity_for("electronic") == record.electronic
    assert record.identity_for("gibbs_free") == record.free_energy
    assert record.other_identity_for("electronic") == record.free_energy

    assert isinstance(record.electronic, EnergyIdentity)
    assert isinstance(record.free_energy, EnergyIdentity)
    with pytest.raises(ValueError):
        BarrierEnergyRecord(species_id="", electronic=record.electronic)
    with pytest.raises(ValueError):
        BarrierEnergyRecord(species_id="x", stoichiometry=0)
    with pytest.raises(ValueError):
        BarrierEnergyRecord.from_dict({"species_id": "x", "energy": {"energy_kind": "enthalpy"}})
