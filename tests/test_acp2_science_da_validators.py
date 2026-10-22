"""A-16a: deterministic transition-state and frequency validators.

Every fixture here is **constructed**.  No optimization, frequency or QC
subprocess ran, and no real transition state is claimed.  Synthetic
ORCA-shaped text is parsed by the existing frequency parser, the frozen
``TaskResult`` documents are built from that parse, and the validators are
pure functions.

The tests pin the distinctions the evidence contract requires:

* exactly one imaginary mode is a *local first-order saddle candidate*, never
  proof of connectivity;
* the specific mode is identified by ORCA's native printed index and
  provenance, never by an array position;
* several imaginary modes refute the first-order candidate;
* missing frequencies are missing evidence, not a refutation;
* a non-converged optimization refutes only that the optimization converged,
  never that no stationary point exists in the sampled region.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from cccp.analysis.research import (
    CLAIM_STATUSES,
    TS_CONNECTIVITY_VALIDATOR_ID,
    TS_MODE_MAPPING_VALIDATOR_ID,
    TS_SADDLE_CANDIDATE_VALIDATOR_ID,
    VALIDATOR_VERSION,
    EnergyIdentity,
    IrcReactionReference,
    MappedModeSelection,
    validate_energy_comparability,
    validate_irc_bidirectional_completion,
    validate_irc_connectivity,
    validate_no_stationary_point_exists,
    validate_optimization_convergence,
    validate_transition_state,
    validate_ts_connectivity,
    validate_ts_frequency_count,
    validate_ts_mapped_mode,
)
from cccp.calculation.frequency_parse import parse_frequency_log
from cccp.calculation.results import TaskResult

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "diels_alder" / "validators"
OPTFREQ = REPO / "tests" / "fixtures" / "acp2" / "science" / "optfreq" / "synthetic"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
        return payload


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result(name: str) -> TaskResult:
    return TaskResult.from_dict(_load_json(FIXTURES / name))


def _selection(name: str) -> dict[str, Any]:
    payload = _load_json(FIXTURES / name)
    selection: dict[str, Any] = payload["selection"]
    return selection


def _statuses(validation: Any) -> dict[str, str]:
    return {claim.claim_id: claim.status for claim in validation.claims}


# ── fixtures are constructed, frozen and reproducible ──────────────────────


def test_validator_fixtures_are_constructed_and_digest_frozen() -> None:
    manifest = _load_json(FIXTURES / "fixture-manifest.json")
    assert manifest["schema"] == "acp2.science.fixture-manifest"
    assert manifest["schema_version"] == 1
    assert manifest["constructed"] is True
    assert manifest["run_key"] is None
    assert manifest["live_validation_gap"]
    assert manifest["thresholds"]["validator_version"] == VALIDATOR_VERSION
    assert set(manifest["thresholds"]["claim_statuses"]) == set(CLAIM_STATUSES)

    for relative, expected in manifest["file_digests"].items():
        assert _sha256_file(FIXTURES / relative) == expected, relative


def test_frequency_parser_reproduces_the_frozen_ts_results() -> None:
    expected = {
        "ts-one-imaginary": ((-520.0, 1642.31, 3817.55), (-520.0,)),
        "ts-multiple-imaginary": ((-520.0, -145.0, 380.0), (-520.0, -145.0)),
        "ts-missing-frequency": ((), ()),
    }
    for name, (frequencies, imaginary) in expected.items():
        analysis = parse_frequency_log(FIXTURES / f"{name}.out")
        assert analysis is not None
        assert analysis.frequencies == frequencies
        assert analysis.imaginary_frequencies == imaginary

        frozen = _result(f"{name}-result.json")
        assert frozen.task is not None
        assert frozen.payload is not None
        assert frozen.payload.analysis == analysis


# ── happy path: one mapped imaginary mode is only a local candidate ────────


def test_ts_one_imaginary_with_mapped_native_mode_is_supported() -> None:
    frequency = _result("ts-one-imaginary-result.json")
    selection = _selection("mode-selection.json")

    count = validate_ts_frequency_count(frequency)
    assert count.status == "supported"
    assert count.details["imaginary_mode_count"] == 1
    assert count.details["imaginary_modes_cm1"] == [-520.0]

    mapped = validate_ts_mapped_mode(frequency, selection)
    assert mapped.status == "supported"
    assert mapped.details["selection_basis"] == "native_printed_index"
    assert mapped.details["native_printed_index"] == 6
    assert mapped.details["selected_mode_frequency_cm1"] == -520.0

    validation = validate_transition_state(frequency, selection)
    assert validation.status == "supported"
    assert validation.classification == "first_order_saddle_candidate"
    assert validation.imaginary_mode_count == 1
    assert validation.mapped_mode_index == 6
    assert validation.candidate.details["connectivity_evaluated"] is False
    assert _statuses(validation) == {
        "c-frequency-hessian-present": "supported",
        "c-ts-exactly-one-imaginary-mode": "supported",
        "c-ts-mapped-imaginary-mode": "supported",
        "c-ts-first-order-saddle-candidate": "supported",
    }

    print("\nQA HAPPY — constructed TS candidate")
    print(f"  classification          {validation.classification}")
    print(f"  imaginary modes (cm-1)  {list(validation.imaginary_modes_cm1)}")
    print(f"  mapped native mode      {validation.mapped_mode_index}")
    print(f"  candidate status        {validation.status}")
    print(f"  connectivity status     {validate_ts_connectivity(validation).status}")


def test_ts_one_imaginary_never_supports_connectivity() -> None:
    validation = validate_transition_state(
        _result("ts-one-imaginary-result.json"),
        _selection("mode-selection.json"),
    )
    assert validation.status == "supported"

    connectivity = validate_ts_connectivity(validation)
    assert connectivity.status == "inconclusive"
    assert connectivity.status != "supported"
    assert connectivity.reason == "missing_evidence"
    assert {check.check_id for check in connectivity.missing_checks} == {"missing_irc"}
    assert connectivity.details["ts_candidate_status"] == "supported"
    assert any("never proves" in note for note in connectivity.notes)


# ── mapped mode: native printed index or rejection ─────────────────────────


def test_ts_mode_lookup_uses_native_printed_index_not_list_position() -> None:
    frequency = _result("ts-one-imaginary-result.json")
    # The frequency list puts the imaginary mode at array position 0, while its
    # native printed index is 6.  Selecting 6 must succeed and selecting the
    # array position must be contradicted.
    assert frequency.frequencies[0] == -520.0
    assert validate_ts_mapped_mode(frequency, MappedModeSelection(6)).status == "supported"

    wrong = validate_ts_mapped_mode(frequency, _selection("mode-selection-wrong-position.json"))
    assert wrong.status == "refuted"
    assert wrong.details["native_printed_index"] == 0
    assert wrong.details["selected_mode_frequency_cm1"] == 0.0


def test_ts_wrong_mode_index_is_refuted() -> None:
    frequency = _result("ts-one-imaginary-result.json")
    wrong = validate_ts_mapped_mode(frequency, _selection("mode-selection-wrong-position.json"))
    assert wrong.status == "refuted"
    assert wrong.reason == "contradiction"
    assert "not imaginary" in wrong.notes[0]

    validation = validate_transition_state(
        frequency, _selection("mode-selection-wrong-position.json")
    )
    assert validation.status == "refuted"
    assert validation.candidate.details["mapped_mode_index"] == 0

    absent_index = validate_ts_mapped_mode(frequency, MappedModeSelection(99))
    assert absent_index.status == "refuted"
    assert "absent" in absent_index.notes[0]

    print("\nQA FAILURE — wrong native mode index")
    print(f"  candidate status        {validation.status}")
    print(f"  mapped-mode reason      {wrong.reason}")


def test_ts_unmapped_mode_is_rejected_not_supported() -> None:
    frequency = _result("ts-one-imaginary-result.json")
    unmapped = validate_ts_mapped_mode(frequency, None)
    assert unmapped.status == "inconclusive"
    assert unmapped.status != "supported"
    assert {check.check_id for check in unmapped.missing_checks} == {"imaginary_mode_missing"}

    validation = validate_transition_state(frequency, None)
    assert validation.status == "inconclusive"
    assert validation.status != "supported"


def test_ts_ambiguous_mapping_status_is_refuted() -> None:
    frequency = _result("ts-one-imaginary-result.json")
    ambiguous = validate_ts_mapped_mode(frequency, _selection("mode-selection-ambiguous.json"))
    assert ambiguous.status == "refuted"
    assert ambiguous.details["mapping_status"] == "ambiguous"
    assert "only a resolved mapping" in ambiguous.notes[0]

    validation = validate_transition_state(frequency, _selection("mode-selection-ambiguous.json"))
    assert validation.status == "refuted"


def test_mode_selection_rejects_array_position_records() -> None:
    with pytest.raises(ValueError, match="native_printed_index"):
        validate_ts_mapped_mode(
            _result("ts-one-imaginary-result.json"),
            _selection("mode-selection-array-index.json"),
        )
    with pytest.raises(ValueError, match="native_printed_index"):
        validate_ts_mapped_mode(_result("ts-one-imaginary-result.json"), {"index": 6})
    with pytest.raises(ValueError):
        MappedModeSelection(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MappedModeSelection(-1)
    with pytest.raises(ValueError):
        MappedModeSelection(6, mapping_status="unknown")


# ── frequency-count classification ─────────────────────────────────────────


def test_ts_multiple_imaginary_modes_refute_first_order_candidate() -> None:
    frequency = _result("ts-multiple-imaginary-result.json")
    count = validate_ts_frequency_count(frequency)
    assert count.status == "refuted"
    assert count.details["classification"] == "higher_order_saddle"
    assert count.details["imaginary_mode_count"] == 2
    assert count.details["imaginary_modes_cm1"] == [-145.0, -520.0]

    selection = {"native_printed_index": 6, "mapping_status": "resolved"}
    validation = validate_transition_state(frequency, selection)
    assert validation.status == "refuted"
    assert validation.candidate.status == "refuted"
    assert validation.imaginary_mode_count == 2
    assert "first-order saddle candidate requires exactly one" in count.notes[0]


def test_ts_missing_frequency_is_inconclusive_never_refuted() -> None:
    frequency = _result("ts-missing-frequency-result.json")
    count = validate_ts_frequency_count(frequency)
    assert count.status == "inconclusive"
    assert count.reason == "missing_evidence"
    assert {check.check_id for check in count.missing_checks} == {"frequency_not_computed"}

    mapped = validate_ts_mapped_mode(frequency, MappedModeSelection(6))
    assert mapped.status == "inconclusive"

    validation = validate_transition_state(frequency, MappedModeSelection(6))
    assert validation.status == "inconclusive"
    assert validation.status != "refuted"
    assert validation.status != "supported"


# ── claim scope: convergence is not an existence statement ─────────────────


def test_nonconverged_optimization_does_not_claim_no_stationary_point() -> None:
    unconverged = TaskResult.from_dict(_load_json(OPTFREQ / "optimize-unconverged-result.json"))
    convergence = validate_optimization_convergence(unconverged)
    assert convergence.status == "refuted"
    assert convergence.details["claim_scope"] == "the reported optimization run only"
    assert any("not evidence that no stationary point exists" in note for note in convergence.notes)

    absence = validate_no_stationary_point_exists(unconverged)
    assert absence.status in {"inconclusive", "not_evaluated"}
    assert absence.status != "refuted"
    assert absence.status != "supported"
    assert absence.validator_id == "check.stationary_point_existence"
    assert absence.statement == "No stationary point exists in the sampled region."
    assert "convergence_not_met" in {check.check_id for check in absence.missing_checks}
    assert "non-converged optimization" in " ".join(absence.notes).lower()


def test_transition_state_with_nonconverged_optimization_is_not_promoted_or_refuted() -> None:
    frequency = _result("ts-one-imaginary-result.json")
    unconverged = TaskResult.from_dict(_load_json(OPTFREQ / "optimize-unconverged-result.json"))
    assert validate_ts_frequency_count(frequency).status == "supported"
    assert validate_ts_mapped_mode(frequency, MappedModeSelection(6)).status == "supported"

    validation = validate_transition_state(
        frequency,
        MappedModeSelection(6),
        optimize_result=unconverged,
    )
    assert validation.status in {"inconclusive", "not_evaluated"}
    assert validation.status not in {"supported", "refuted"}
    assert validation.candidate.details["convergence_status"] == "refuted"
    assert "c-optimization-converged" in _statuses(validation)


def test_ts_connectivity_with_irc_result_defers_to_a16b() -> None:
    validation = validate_transition_state(
        _result("ts-one-imaginary-result.json"),
        MappedModeSelection(6),
    )
    connectivity = validate_ts_connectivity(validation, irc_result=object())
    assert connectivity.status == "not_evaluated"
    assert connectivity.validator_id == TS_CONNECTIVITY_VALIDATOR_ID
    assert {check.check_id for check in connectivity.missing_checks} == {"validator_unavailable"}


# ── report shape ───────────────────────────────────────────────────────────


def test_validator_outputs_are_json_safe_and_namespaced() -> None:
    validation = validate_transition_state(
        _result("ts-one-imaginary-result.json"),
        _selection("mode-selection.json"),
    )
    report = validation.to_dict()
    assert json.loads(json.dumps(report)) == report
    assert report["status"] == "supported"
    assert report["validator_version"] == VALIDATOR_VERSION
    for claim in report["claims"]:
        assert claim["validator"]["validator_id"].startswith("check.")
        assert claim["validator"]["kind"] == "deterministic"

    ids = {claim.claim_id for claim in validation.claims}
    assert {
        "c-ts-exactly-one-imaginary-mode",
        "c-ts-mapped-imaginary-mode",
        "c-ts-first-order-saddle-candidate",
    } <= ids
    assert validation.claim("c-ts-mapped-imaginary-mode") is not None
    assert validate_ts_frequency_count(None).status == "not_evaluated"
    assert validate_no_stationary_point_exists(None).status == "not_evaluated"
    assert TS_SADDLE_CANDIDATE_VALIDATOR_ID == "check.ts_saddle_candidate"
    assert TS_MODE_MAPPING_VALIDATOR_ID == "check.ts_mode_mapping"


# ── A-16b: bidirectional IRC endpoint/connectivity ─────────────────────────


def _reference() -> IrcReactionReference:
    return IrcReactionReference.from_dict(_load_json(FIXTURES / "irc-endpoint-reference.json"))


def _energy_cases() -> dict[str, Any]:
    return _load_json(FIXTURES / "energy-comparability-cases.json")["cases"]


def test_constructed_irc_reference_covers_every_atom_once() -> None:
    reference = _reference()
    assert reference.reaction_id.endswith("butadiene_ethene_cyclohexene.v1")
    assert reference.mapping_dict == {left: right for left, right in reference.atom_mapping}
    assert len(reference.atom_mapping) == len(reference.reactant.symbols) == 16
    assert reference.reactant.atom_ids[0] == "butadiene:bd_c1"
    assert reference.product.atom_ids[-1] == "cyclohexene:cy_h10"
    for left, right in reference.atom_mapping:
        assert reference.reactant.symbols[left] == reference.product.symbols[right]


def test_two_direction_irc_supports_connectivity_with_endpoint_mapping() -> None:
    reference = _reference()
    irc = _result("irc-two-direction.json")

    completion = validate_irc_bidirectional_completion(irc)
    assert completion.status == "supported"
    assert completion.details["forward"]["steps"] == 12
    assert completion.details["reverse"]["steps"] == 14
    assert completion.details["direction_status"] == {
        "forward": "completed",
        "reverse": "completed",
    }

    validation = validate_irc_connectivity(irc, reference)
    assert validation.status == "supported"
    assert validation.connectivity.status == "supported"
    assert validation.direction_assignment == "swapped"
    assert validation.forward_resolution is not None
    assert validation.reverse_resolution is not None
    assert validation.forward_resolution.target_endpoint_id == "product"
    assert validation.reverse_resolution.target_endpoint_id == "reactant"
    assert validation.forward_resolution.atom_order_stability == "permutation_resolved"
    assert validation.reverse_resolution.atom_order_stability == "identity"
    assert dict(validation.effective_atom_mapping) == reference.mapping_dict

    print("\nQA HAPPY — constructed bidirectional IRC")
    print(f"  connectivity status     {validation.status}")
    print(f"  direction assignment    {validation.direction_assignment}")
    for direction, resolution in (
        ("forward", validation.forward_resolution),
        ("reverse", validation.reverse_resolution),
    ):
        print(
            f"  {direction:<7} target        {resolution.target_endpoint_id} "
            f"(rmsd {resolution.rmsd_angstrom:.6f} A, order {resolution.atom_order_stability})"
        )
    print(f"  effective mapping       {list(validation.effective_atom_mapping)}")


def test_one_sided_irc_is_inconclusive_never_supported() -> None:
    reference = _reference()
    irc = _result("irc-one-direction.json")

    completion = validate_irc_bidirectional_completion(irc)
    assert completion.status == "inconclusive"
    assert completion.status != "supported"
    assert {check.check_id for check in completion.missing_checks} == {"irc_not_bidirectional"}

    validation = validate_irc_connectivity(irc, reference)
    assert validation.status == "inconclusive"
    assert validation.status not in {"supported", "refuted"}
    assert {check.check_id for check in validation.connectivity.missing_checks} == {
        "irc_not_bidirectional"
    }
    assert validation.reverse.present is False

    print("\nQA FAILURE — one IRC direction dropped")
    print(f"  connectivity status     {validation.status}")
    print(
        f"  missing checks          {[c.check_id for c in validation.connectivity.missing_checks]}"
    )


def test_wrong_endpoint_is_refuted() -> None:
    reference = _reference()
    validation = validate_irc_connectivity(_result("irc-wrong-endpoint.json"), reference)
    assert validation.status == "refuted"
    assert validation.connectivity.reason == "contradiction"
    assert validation.forward_resolution is not None
    assert validation.reverse_resolution is not None
    assert validation.forward_resolution.target_endpoint_id == "product"
    assert validation.reverse_resolution.status == "different"
    assert validation.reverse_resolution.target_endpoint_id is None
    assert validation.claim("c-irc-reverse-endpoint") is not None
    assert validation.claim("c-irc-reverse-endpoint").status == "refuted"


def test_atom_order_permutation_does_not_create_a_false_connection() -> None:
    reference = _reference()
    wrong = validate_irc_connectivity(_result("irc-wrong-endpoint-permuted.json"), reference)
    assert wrong.status == "refuted"
    assert wrong.connectivity.status != "supported"

    correct = validate_irc_connectivity(
        _result("irc-two-direction-permuted-endpoint.json"), reference
    )
    assert correct.status == "supported"
    assert correct.reverse_resolution is not None
    assert correct.reverse_resolution.atom_order_stability == "permutation_resolved"
    assert correct.forward_resolution is not None
    assert correct.forward_resolution.atom_order_stability == "permutation_resolved"
    assert dict(correct.effective_atom_mapping) == reference.mapping_dict
    assert correct.claim("c-irc-atom-order-stable").status == "supported"


def test_direction_swap_is_allowed_only_when_the_mapping_proves_the_target() -> None:
    reference = _reference()
    swapped = validate_irc_connectivity(_result("irc-swapped-two-direction.json"), reference)
    assert swapped.status == "supported"
    assert swapped.direction_assignment == "nominal"
    assert swapped.forward_resolution is not None
    assert swapped.forward_resolution.target_endpoint_id == "reactant"
    assert swapped.reverse_resolution is not None
    assert swapped.reverse_resolution.target_endpoint_id == "product"
    assert dict(swapped.effective_atom_mapping) == reference.mapping_dict

    # A bijective but wrong target mapping (two hydrogens swapped) must refute
    # the same endpoint evidence: labels/geometry alone are not the proof.
    wrong_pairs = list(reference.atom_mapping)
    product_for_reactant = {left: right for left, right in wrong_pairs}
    swapped_reactants = [
        index for index, _ in wrong_pairs if reference.reactant.symbols[index] == "H"
    ][:2]
    left_a, left_b = swapped_reactants
    right_a = product_for_reactant[left_a]
    right_b = product_for_reactant[left_b]
    wrong_pairs = [
        (left, right_b if left == left_a else right_a if left == left_b else right)
        for left, right in wrong_pairs
    ]
    wrong_reference = IrcReactionReference(
        reactant=reference.reactant,
        product=reference.product,
        atom_mapping=tuple(wrong_pairs),
        reaction_id=reference.reaction_id,
    )
    refused = validate_irc_connectivity(_result("irc-swapped-two-direction.json"), wrong_reference)
    assert refused.status == "refuted"
    assert "does not reproduce the frozen" in " ".join(refused.connectivity.notes)


def test_ts_connectivity_delegates_to_irc_validation_with_a_reference() -> None:
    validation = validate_transition_state(
        _result("ts-one-imaginary-result.json"),
        MappedModeSelection(6),
    )
    connectivity = validate_ts_connectivity(
        validation,
        irc_result=_result("irc-two-direction.json"),
        reference=_reference(),
    )
    assert connectivity.status == "supported"
    assert connectivity.validator_id == TS_CONNECTIVITY_VALIDATOR_ID

    refused = validate_ts_connectivity(
        validation,
        irc_result=_result("irc-two-direction.json"),
    )
    assert refused.status == "not_evaluated"
    assert {check.check_id for check in refused.missing_checks} == {"validator_unavailable"}


def test_irc_connectivity_report_is_json_safe() -> None:
    validation = validate_irc_connectivity(_result("irc-two-direction.json"), _reference())
    report = validation.to_dict()
    assert json.loads(json.dumps(report)) == report
    assert report["status"] == "supported"
    assert report["direction_assignment"] == "swapped"
    assert len(report["effective_atom_mapping"]) == 16
    assert report["forward_endpoint"]["target_endpoint_id"] == "product"
    assert report["reverse_endpoint"]["target_endpoint_id"] == "reactant"
    for claim in report["claims"]:
        assert claim["validator"]["validator_id"].startswith("check.")
        assert claim["validator"]["kind"] == "deterministic"


# ── A-16b: energy comparability ────────────────────────────────────────────


def test_energy_comparability_cases_are_refused_or_supported_as_declared() -> None:
    cases = _energy_cases()
    for name, case in cases.items():
        assessment = validate_energy_comparability(case["left"], case["right"])
        assert assessment.status == case["expected_status"], (name, assessment.to_dict())
        assert assessment.details["subtraction_performed"] is False
        assert assessment.validator_id == "check.energy_comparability"

    comparable = validate_energy_comparability(
        cases["comparable"]["left"], cases["comparable"]["right"]
    )
    assert comparable.status == "supported"
    assert comparable.details["standard_state_lexicon"]["left_normalized"] == "1atm"
    assert comparable.details["standard_state_lexicon"]["comparison_basis"] == (
        "declared token, not the collapsed lexicon token"
    )


def test_different_method_state_or_reference_is_refused() -> None:
    expected = {
        "different_method": "method",
        "different_electronic_state": "electronic_state.state_id",
        "different_standard_state": "standard_state",
        "different_reference_state": "reference_state",
        "wrong_charge": "charge",
        "mixed_energy_kind": "energy_kind",
        "different_unit": "energy_unit",
    }
    cases = _energy_cases()
    for name, field_name in expected.items():
        assessment = validate_energy_comparability(cases[name]["left"], cases[name]["right"])
        assert assessment.status == "refuted", name
        assert field_name in assessment.details["differing_fields"], name
        assert "cannot be subtracted" in " ".join(assessment.notes)


def test_missing_identity_is_inconclusive_and_missing_energy_not_evaluated() -> None:
    cases = _energy_cases()
    missing_standard = validate_energy_comparability(
        cases["missing_standard_state"]["left"], cases["missing_standard_state"]["right"]
    )
    assert missing_standard.status == "inconclusive"
    assert "standard_state_unspecified" in {
        check.check_id for check in missing_standard.missing_checks
    }

    missing_state = validate_energy_comparability(
        cases["missing_state"]["left"], cases["missing_state"]["right"]
    )
    assert missing_state.status == "inconclusive"
    assert "electronic_state_unresolved" in {
        check.check_id for check in missing_state.missing_checks
    }

    missing_energy = validate_energy_comparability(
        cases["missing_energy"]["left"], cases["missing_energy"]["right"]
    )
    assert missing_energy.status == "not_evaluated"
    assert missing_energy.status != "inconclusive"
    assert {check.check_id for check in missing_energy.missing_checks} == {"not_executed"}

    assert validate_energy_comparability(None, None).status == "not_evaluated"


def test_energy_identity_round_trips_and_rejects_bad_records() -> None:
    identity = EnergyIdentity.from_dict(_energy_cases()["comparable"]["left"])
    assert identity.to_dict() == _energy_cases()["comparable"]["left"]
    assert json.loads(json.dumps(identity.to_dict())) == identity.to_dict()
    with pytest.raises(ValueError):
        EnergyIdentity(energy_kind="not_a_kind")
    with pytest.raises(ValueError):
        EnergyIdentity(energy_unit="hartree_per_atom")
    with pytest.raises(ValueError):
        EnergyIdentity(temperature_k=0.0)
